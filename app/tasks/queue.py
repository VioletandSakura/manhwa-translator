from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.domain.enums import PageStatus, TaskStatus
from app.domain.models import PageRecord
from app.security.redaction import redact_text
from app.storage.database import Database
from app.tasks.control import checkpoint_handler

PageProgress = Callable[[str, int], None]
PageProcessor = Callable[[PageRecord, Path, PageProgress], Awaitable[Any]]
QueueEventCallback = Callable[[str, PageRecord], None]
QueueProgressCallback = Callable[[str, PageRecord, str, int], None]
QueueTaskCallback = Callable[[str], None]


class QueueStopped(Exception):
    """当前步骤安全结束后停止，留下可恢复页面。"""


class PersistentTaskQueue:
    """暂停在步骤边界等待；取消/退出不再开始新步骤或自动重试。"""

    def __init__(self, database: Database, processor: PageProcessor, *,
                 max_concurrent_pages: int = 1, max_page_attempts: int = 1,
                 page_retry_delay_seconds: float = 1.0,
                 on_task_completed: QueueTaskCallback | None = None,
                 on_page_completed: QueueEventCallback | None = None,
                 on_page_failed: QueueEventCallback | None = None,
                 on_page_progress: QueueProgressCallback | None = None,
                 on_run_finished: Callable[[], Awaitable[None]] | None = None) -> None:
        self.database = database
        self.processor = processor
        self.max_concurrent_pages = max(1, max_concurrent_pages)
        self.max_page_attempts = max(1, max_page_attempts)
        self.page_retry_delay_seconds = max(0.0, page_retry_delay_seconds)
        self.on_task_completed = on_task_completed
        self.on_page_completed = on_page_completed
        self.on_page_failed = on_page_failed
        self.on_page_progress = on_page_progress
        self.on_run_finished = on_run_finished
        self._run_events: dict[str, threading.Event] = {}
        self._paused_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()
        self._stopped_tasks: set[str] = set()
        self._running_tasks: set[str] = set()

    def pause(self, task_id: str) -> None:
        self._paused_tasks.add(task_id)
        self._event(task_id).clear()
        self.database.update_task_status(task_id, TaskStatus.PAUSED)

    def resume(self, task_id: str) -> bool:
        if not self.database.list_resumable_pages(task_id):
            self.database.repair_completed_task_statuses()
            return False
        self._paused_tasks.discard(task_id)
        self._event(task_id).set()
        self.database.update_task_status(task_id, TaskStatus.RUNNING)
        return True

    def cancel(self, task_id: str) -> None:
        self._cancelled_tasks.add(task_id)
        self._event(task_id).set()
        self.database.update_task_status(task_id, TaskStatus.CANCELLED)

    def stop(self, task_id: str) -> None:
        """退出应用：完成当前不可中断步骤后，把未完成页面留作待恢复。"""
        self._stopped_tasks.add(task_id)
        self._event(task_id).set()
        self.database.update_task_status(task_id, TaskStatus.PAUSED)

    def retry_page(self, page_id: str) -> None:
        page = self.database.get_page(page_id)
        if page.task_id in self._running_tasks:
            raise ValueError("请等待当前任务安全结束后再重试")
        if page.status not in {PageStatus.FAILED, PageStatus.CANCELLED}:
            raise ValueError("只能重试失败或已取消的页面")
        self.database.reset_page_for_retry(page_id)
        self._cancelled_tasks.discard(page.task_id)
        self._stopped_tasks.discard(page.task_id)
        self._paused_tasks.discard(page.task_id)
        self.database.update_task_status(page.task_id, TaskStatus.PENDING)

    async def checkpoint(self, task_id: str) -> None:
        while True:
            if task_id in self._cancelled_tasks or task_id in self._stopped_tasks:
                raise QueueStopped()
            if self._event(task_id).is_set():
                return
            await asyncio.sleep(0.05)

    async def run(self, task_id: str, *, page_ids: list[str] | None = None) -> None:
        if task_id in self._running_tasks:
            raise ValueError("该任务已经在运行")
        self._running_tasks.add(task_id)
        jobs: list[asyncio.Task] = []
        try:
            task = self.database.get_task(task_id)
            pages = self.database.list_resumable_pages(task_id)
            if page_ids is not None:
                pages = [page for page in pages if page.id in page_ids]
                if {page.id for page in pages} != set(page_ids):
                    raise ValueError("单页重试的页面不属于此任务或不可重试")
            if task_id not in self._paused_tasks:
                self._event(task_id).set()
                self.database.update_task_status(task_id, TaskStatus.RUNNING)
            # A semaphore limits processing but still allocates one suspended
            # Task per page. Keep only the configured number of workers alive.
            pending = iter(pages)

            async def worker() -> None:
                for page in pending:
                    await self._process_page(task_id, task, page)

            jobs = [asyncio.create_task(worker()) for _ in range(min(self.max_concurrent_pages, len(pages)))]
            await asyncio.gather(*jobs)
            self._finish_task(task_id)
            if self.on_task_completed is not None:
                self.on_task_completed(task_id)
        except BaseException:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            for page in self.database.list_task_pages(task_id):
                if page.status is PageStatus.RUNNING:
                    self.database.update_page_status(page.id, PageStatus.PENDING,
                                                     stage="处理被中断，等待恢复", progress=0)
            self.database.update_task_status(task_id, TaskStatus.PAUSED)
            raise
        finally:
            self._running_tasks.discard(task_id)
            try:
                if self.on_run_finished is not None:
                    await self.on_run_finished()
            finally:
                self.database.close()

    async def _process_page(self, task_id, task, page) -> None:
        control_token = checkpoint_handler.set(lambda: self.checkpoint(task_id))
        try:
            await self.checkpoint(task_id)
            last_error = None
            result = None
            for attempt in range(self.max_page_attempts):
                await self.checkpoint(task_id)
                self.database.increment_page_attempt(page.id)
                self.database.update_page_status(page.id, PageStatus.RUNNING,
                    stage="准备" if attempt == 0 else f"自动重试 {attempt}", progress=0)

                def record_progress(stage: str, progress: int) -> None:
                    self.database.update_page_status(page.id, PageStatus.RUNNING, stage=stage, progress=progress)
                    if self.on_page_progress is not None:
                        self.on_page_progress(task_id, self.database.get_page(page.id), stage, progress)

                # Preserve the three-argument processor API; the pipeline can
                # await this checkpoint between costly steps.
                record_progress.checkpoint = lambda: self.checkpoint(task_id)
                try:
                    result = await self.processor(page, Path(task.output_dir), record_progress)
                    last_error = None
                    break
                except QueueStopped:
                    raise
                except Exception as error:
                    last_error = error
                    if getattr(error, "retryable", True) is False:
                        break
                    if attempt + 1 < self.max_page_attempts:
                        deadline = asyncio.get_running_loop().time() + self._page_retry_delay(attempt)
                        while asyncio.get_running_loop().time() < deadline:
                            await self.checkpoint(task_id)
                            await asyncio.sleep(0.05)
            if last_error is not None:
                await self.checkpoint(task_id)
                self.database.update_page_status(page.id, PageStatus.FAILED, stage="失败", progress=0,
                                                 error=redact_text(str(last_error)))
                if self.on_page_failed is not None:
                    self.on_page_failed(task_id, self.database.get_page(page.id))
                return
            result_path = getattr(getattr(result, "artifacts", None), "translated", None)
            self.database.set_page_result(page.id, None if result_path is None else str(result_path))
            self.database.update_page_status(page.id, PageStatus.COMPLETED, stage="保存", progress=100)
            if self.on_page_completed is not None:
                self.on_page_completed(task_id, self.database.get_page(page.id))
        except QueueStopped:
            cancelled = task_id in self._cancelled_tasks
            self.database.update_page_status(page.id, PageStatus.CANCELLED if cancelled else PageStatus.PENDING,
                stage="已取消" if cancelled else "安全停止，等待恢复", progress=0)
        finally:
            checkpoint_handler.reset(control_token)

    def _page_retry_delay(self, attempt: int) -> float:
        return min(30.0, self.page_retry_delay_seconds * (2**attempt))

    def _finish_task(self, task_id: str) -> None:
        pages = self.database.list_task_pages(task_id)
        if pages and all(page.status is PageStatus.COMPLETED for page in pages):
            status = TaskStatus.COMPLETED
        elif task_id in self._cancelled_tasks or any(page.status is PageStatus.CANCELLED for page in pages):
            status = TaskStatus.CANCELLED
        elif task_id in self._stopped_tasks or task_id in self._paused_tasks:
            status = TaskStatus.PAUSED
        elif any(page.status is PageStatus.FAILED for page in pages):
            status = TaskStatus.FAILED
        else:
            status = TaskStatus.PENDING
        self.database.update_task_status(task_id, status)

    def _event(self, task_id: str) -> threading.Event:
        if task_id not in self._run_events:
            self._run_events[task_id] = threading.Event()
        return self._run_events[task_id]
