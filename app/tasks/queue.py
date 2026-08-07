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


PageProgress = Callable[[str, int], None]
PageProcessor = Callable[[PageRecord, Path, PageProgress], Awaitable[Any]]
QueueEventCallback = Callable[[str, PageRecord], None]
QueueProgressCallback = Callable[[str, PageRecord, str, int], None]
QueueTaskCallback = Callable[[str], None]


class PersistentTaskQueue:
    def __init__(
        self,
        database: Database,
        processor: PageProcessor,
        *,
        max_concurrent_pages: int = 1,
        max_page_attempts: int = 1,
        page_retry_delay_seconds: float = 1.0,
        on_task_completed: QueueTaskCallback | None = None,
        on_page_completed: QueueEventCallback | None = None,
        on_page_failed: QueueEventCallback | None = None,
        on_page_progress: QueueProgressCallback | None = None,
    ) -> None:
        self.database = database
        self.processor = processor
        self.max_concurrent_pages = max(1, max_concurrent_pages)
        self.max_page_attempts = max(1, max_page_attempts)
        self.page_retry_delay_seconds = max(0.0, page_retry_delay_seconds)
        self.on_task_completed = on_task_completed
        self.on_page_completed = on_page_completed
        self.on_page_failed = on_page_failed
        self.on_page_progress = on_page_progress
        self._run_events: dict[str, threading.Event] = {}
        self._paused_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()

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

    def retry_page(self, page_id: str) -> None:
        page = self.database.get_page(page_id)
        self.database.reset_page_for_retry(page_id)
        self._cancelled_tasks.discard(page.task_id)
        self.database.update_task_status(page.task_id, TaskStatus.PENDING)

    async def run(self, task_id: str) -> None:
        task = self.database.get_task(task_id)
        event = self._event(task_id)
        if task_id not in self._paused_tasks:
            event.set()
            self.database.update_task_status(task_id, TaskStatus.RUNNING)
        pages = self.database.list_resumable_pages(task_id)
        semaphore = asyncio.Semaphore(self.max_concurrent_pages)

        async def process_page(page: PageRecord) -> None:
            async with semaphore:
                await self._process_page(task_id, task, page, event)

        await asyncio.gather(*(process_page(page) for page in pages))
        self._finish_task(task_id)
        if self.on_task_completed is not None:
            self.on_task_completed(task_id)
        self.database.close()

    async def _process_page(
        self,
        task_id: str,
        task,
        page: PageRecord,
        event: threading.Event,
    ) -> None:
        await asyncio.to_thread(event.wait)
        if task_id in self._cancelled_tasks:
            self.database.update_page_status(
                page.id, PageStatus.CANCELLED, stage="已取消", progress=page.progress
            )
            return
        last_error: Exception | None = None
        result = None
        for attempt in range(self.max_page_attempts):
            self.database.increment_page_attempt(page.id)
            stage = "准备" if attempt == 0 else f"自动重试 {attempt}"
            self.database.update_page_status(page.id, PageStatus.RUNNING, stage=stage, progress=0)

            def record_progress(stage: str, progress: int, *, page_id: str = page.id) -> None:
                self.database.update_page_status(page_id, PageStatus.RUNNING, stage=stage, progress=progress)
                if self.on_page_progress is not None:
                    self.on_page_progress(task_id, self.database.get_page(page_id), stage, progress)

            try:
                result = await self.processor(page, Path(task.output_dir), record_progress)
                last_error = None
                break
            except Exception as error:
                last_error = error
                if attempt + 1 < self.max_page_attempts:
                    await asyncio.sleep(self._page_retry_delay(attempt))
        if last_error is not None:
            message = redact_text(str(last_error))
            self.database.update_page_status(
                page.id, PageStatus.FAILED, stage="失败", progress=0, error=message
            )
            if self.on_page_failed is not None:
                self.on_page_failed(task_id, self.database.get_page(page.id))
            return
        result_path = getattr(getattr(result, "artifacts", None), "translated", None)
        self.database.set_page_result(page.id, None if result_path is None else str(result_path))
        self.database.update_page_status(page.id, PageStatus.COMPLETED, stage="保存", progress=100)
        if self.on_page_completed is not None:
            self.on_page_completed(task_id, self.database.get_page(page.id))

    def _page_retry_delay(self, attempt: int) -> float:
        return min(30.0, self.page_retry_delay_seconds * (2**attempt))

    def _finish_task(self, task_id: str) -> None:
        pages = self.database.list_task_pages(task_id)
        if task_id in self._cancelled_tasks or any(page.status is PageStatus.CANCELLED for page in pages):
            status = TaskStatus.CANCELLED
        elif any(page.status is PageStatus.FAILED for page in pages):
            status = TaskStatus.FAILED
        elif pages and all(page.status is PageStatus.COMPLETED for page in pages):
            status = TaskStatus.COMPLETED
        else:
            status = TaskStatus.PAUSED if task_id in self._paused_tasks else TaskStatus.PENDING
        self.database.update_task_status(task_id, status)

    def _event(self, task_id: str) -> threading.Event:
        event = self._run_events.get(task_id)
        if event is None:
            event = threading.Event()
            self._run_events[task_id] = event
        return event
