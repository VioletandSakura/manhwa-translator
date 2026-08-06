from __future__ import annotations

import asyncio
from pathlib import Path

from app.domain.enums import PageStatus, TaskStatus
from app.storage.database import Database
from app.tasks.queue import PersistentTaskQueue


def _task_with_pages(database: Database, count: int = 2) -> tuple[str, list[str]]:
    task_id = database.create_task("chapter", "out")
    page_ids = [database.add_page(task_id, index, f"{index + 1}.png") for index in range(count)]
    return task_id, page_ids


def test_queue_resumes_without_reprocessing_completed_pages(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, page_ids = _task_with_pages(database)
    database.update_page_status(page_ids[0], PageStatus.COMPLETED, stage="保存", progress=100)
    processed: list[str] = []

    async def process(page, _output_dir, on_progress):
        processed.append(page.id)
        on_progress("OCR", 30)
        return f"result-{page.id}"

    queue = PersistentTaskQueue(database, process)
    asyncio.run(queue.run(task_id))

    assert processed == [page_ids[1]]
    assert database.get_task(task_id).status is TaskStatus.COMPLETED


def test_queue_can_pause_before_page_and_continue(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, _ = _task_with_pages(database, 1)
    processed = asyncio.Event()

    async def scenario() -> None:
        async def process(page, _output_dir, on_progress):
            processed.set()

        queue = PersistentTaskQueue(database, process)
        queue.pause(task_id)
        running = asyncio.create_task(queue.run(task_id))
        await asyncio.sleep(0)
        assert not processed.is_set()
        assert database.get_task(task_id).status is TaskStatus.PAUSED
        queue.resume(task_id)
        await running

    asyncio.run(scenario())
    assert processed.is_set()


def test_queue_refuses_to_resume_task_with_only_completed_pages(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, page_ids = _task_with_pages(database, 1)
    database.update_page_status(page_ids[0], PageStatus.COMPLETED, stage="保存", progress=100)
    database.update_task_status(task_id, TaskStatus.RUNNING)
    queue = PersistentTaskQueue(database, lambda *_args: None)

    resumed = queue.resume(task_id)

    assert resumed is False
    assert database.get_task(task_id).status is TaskStatus.COMPLETED


def test_failed_page_can_be_retried_and_attempt_count_is_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, page_ids = _task_with_pages(database, 1)
    calls = 0

    async def process(page, _output_dir, on_progress):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")

    queue = PersistentTaskQueue(database, process)
    asyncio.run(queue.run(task_id))
    assert database.get_page(page_ids[0]).status is PageStatus.FAILED
    assert database.get_task(task_id).status is TaskStatus.FAILED

    queue.retry_page(page_ids[0])
    asyncio.run(queue.run(task_id))

    page = database.get_page(page_ids[0])
    assert page.status is PageStatus.COMPLETED
    assert page.attempt_count == 2


def test_queue_can_process_pages_concurrently_when_configured(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, page_ids = _task_with_pages(database, 4)
    active = 0
    max_active = 0

    async def process(page, _output_dir, on_progress):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        on_progress("OCR", 30)
        return f"result-{page.id}"

    queue = PersistentTaskQueue(database, process, max_concurrent_pages=2)
    asyncio.run(queue.run(task_id))

    assert max_active == 2
    assert database.get_task(task_id).status is TaskStatus.COMPLETED


def test_queue_default_remains_sequential(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id, _ = _task_with_pages(database, 4)
    active = 0
    max_active = 0

    async def process(page, _output_dir, on_progress):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return f"result-{page.id}"

    queue = PersistentTaskQueue(database, process)
    asyncio.run(queue.run(task_id))

    assert max_active == 1
    assert database.get_task(task_id).status is TaskStatus.COMPLETED
