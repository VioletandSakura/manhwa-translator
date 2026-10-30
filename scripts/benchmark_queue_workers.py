"""Isolated scheduler benchmark: synthetic pages, no OCR, disk writes or API.

Use --queue-source to compare a saved queue.py with the current implementation.
Elapsed times include tracemalloc overhead and do not predict translation speed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.domain.enums import PageStatus, TaskStatus
from app.domain.models import PageRecord
from app.tasks.queue import PersistentTaskQueue


class MemoryDatabase:
    """Keep the same per-page state contract while excluding SQLite I/O."""

    def __init__(self, count):
        self.task = SimpleNamespace(output_dir="synthetic", status=TaskStatus.PENDING)
        self.pages = {
            str(index): PageRecord(str(index), "task", index, f"{index}.png", PageStatus.PENDING, "", 0, None)
            for index in range(count)
        }

    def get_task(self, _task):
        return self.task

    def list_resumable_pages(self, _task):
        return list(self.pages.values())

    list_task_pages = list_resumable_pages

    def update_task_status(self, _task, status):
        self.task.status = status

    def increment_page_attempt(self, page_id):
        self.pages[page_id] = replace(self.pages[page_id], attempt_count=self.pages[page_id].attempt_count + 1)

    def update_page_status(self, page_id, status, **values):
        self.pages[page_id] = replace(self.pages[page_id], status=status, **values)

    def set_page_result(self, page_id, path):
        self.pages[page_id] = replace(self.pages[page_id], result_path=path)

    def close(self):
        pass


async def measure(queue_type, count, concurrency):
    database = MemoryDatabase(count)
    processed = []
    created = 0
    original_create_task = asyncio.create_task

    def create_task(coro, **kwargs):
        nonlocal created
        created += 1
        return original_create_task(coro, **kwargs)

    async def process(page, _output, _progress):
        await asyncio.sleep(0)
        processed.append(page.page_index)
        return SimpleNamespace(artifacts=SimpleNamespace(translated=Path(f"{page.page_index}.png")))

    queue = queue_type(database, process, max_concurrent_pages=concurrency)
    asyncio.create_task = create_task
    tracemalloc.start()
    started = time.perf_counter()
    try:
        await queue.run("task")
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
        asyncio.create_task = original_create_task
    assert sorted(processed) == list(range(count))
    assert database.task.status is TaskStatus.COMPLETED
    assert all(page.status is PageStatus.COMPLETED and page.attempt_count == 1 for page in database.pages.values())
    state = "\n".join(f"{page.page_index}:{page.status.value}:{page.attempt_count}:{page.result_path}" for page in database.pages.values())
    return {"elapsed_seconds": elapsed, "peak_python_bytes": peak, "created_tasks": created,
            "output_sha256": hashlib.sha256(state.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-source", type=Path)
    parser.add_argument("--pages", type=int, default=10000)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.pages, args.concurrency, args.repeat) < 1:
        parser.error("pages, concurrency and repeat must be positive")
    queue_type = PersistentTaskQueue
    if args.queue_source:
        spec = importlib.util.spec_from_file_location("benchmark_saved_queue", args.queue_source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        queue_type = module.PersistentTaskQueue
    runs = [asyncio.run(measure(queue_type, args.pages, args.concurrency)) for _ in range(args.repeat)]
    assert len({run["output_sha256"] for run in runs}) == 1
    report = {
        "workload": "synthetic scheduler only; no SQLite, OCR, images, or network; tracemalloc enabled",
        "queue_source": str(args.queue_source or ROOT / "app/tasks/queue.py"),
        "pages": args.pages, "concurrency": args.concurrency, "runs": runs,
        "median_seconds": statistics.median(run["elapsed_seconds"] for run in runs),
        "median_peak_python_bytes": statistics.median(run["peak_python_bytes"] for run in runs),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
