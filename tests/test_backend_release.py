from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.domain.enums import PageStatus, TaskStatus
from app.importing.importer import InputImporter
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database
from app.tasks.pipeline import PagePipeline, PipelineOptions
from app.tasks.queue import PersistentTaskQueue
from test_application_services import MemoryCredentialBackend


def _source(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_import_preserves_same_stem_pages_and_keeps_unambiguous_paths(tmp_path):
    sources = [
        _source(tmp_path / "one/01.png", b"first"),
        _source(tmp_path / "two/01.jpg", b"second"),
        _source(tmp_path / "three/01.PNG", b"third"),
        _source(tmp_path / "four/cover.png", b"fourth"),
        _source(tmp_path / "five/COVER.jpg", b"fifth"),
        _source(tmp_path / "six/000001_01.png", b"reserved generated name"),
        _source(tmp_path / "six/unique.webp", b"unchanged"),
    ]
    batch = InputImporter(tmp_path / "imports").import_sources(sources)

    assert len({page.source_path.stem.casefold() for page in batch.pages}) == len(sources)
    assert [page.source_path.read_bytes() for page in batch.pages] == [path.read_bytes() for path in sources]
    assert [page.original_name for page in batch.pages] == [path.name for path in sources]
    assert batch.pages[-1].source_path == sources[-1]
    assert batch.workspace is not None


def _book(path: Path, content: bytes, extension: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as book:
        if extension == ".epub":
            book.writestr("META-INF/container.xml", '<container><rootfile full-path="book.opf"/></container>')
            book.writestr("book.opf", '<package><manifest><item id="p" href="01.png" media-type="image/png"/></manifest><spine><itemref idref="p"/></spine></package>')
        book.writestr("01.png", content)
    return path


@pytest.mark.parametrize("extension", [".zip", ".epub"])
def test_same_named_archives_from_different_directories_never_overwrite(tmp_path, extension):
    sources = [_book(tmp_path / str(index) / f"book{extension}", bytes([index]), extension) for index in range(3)]
    batch = InputImporter(tmp_path / "imports").import_sources(sources)

    assert [page.source_path.read_bytes() for page in batch.pages] == [b"\0", b"\1", b"\2"]
    assert [page.original_name for page in batch.pages] == ["01.png"] * 3
    assert len({page.source_path.stem.casefold() for page in batch.pages}) == 3


def test_archive_generated_duplicate_name_cannot_overwrite_an_existing_member(tmp_path):
    source = tmp_path / "book.zip"
    with zipfile.ZipFile(source, "w") as book:
        book.writestr("a/01.png", b"first")
        book.writestr("a/000002_01.png", b"second")
        book.writestr("b/01.png", b"third")
    batch = InputImporter(tmp_path / "imports").import_sources([source])

    assert [page.source_path.read_bytes() for page in batch.pages] == [b"first", b"second", b"third"]
    assert len({page.source_path.stem.casefold() for page in batch.pages}) == 3


def test_unambiguous_images_are_not_copied_to_a_workspace(tmp_path):
    source = _source(tmp_path / "01.png", b"one")
    batch = InputImporter(tmp_path / "imports").import_sources([source])
    assert batch.workspace is None
    assert batch.pages[0].source_path == source


class _EmptyOCR:
    async def detect(self, image):
        return []

    async def recognize(self, image, regions):
        return regions


class _NeverUsed:
    async def translate(self, *args):
        pytest.fail("offline fixture must not call a paid service")

    async def inpaint(self, *args):
        pytest.fail("empty fixture must not inpaint")

    async def render(self, *args):
        pytest.fail("empty fixture must not render text")


def test_collision_batch_outputs_archive_and_edit_sources_keep_each_page(tmp_path, monkeypatch):
    app = TranslationApplication(
        AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp"),
        credentials=CredentialStore(MemoryCredentialBackend()),
    )
    app.initialize()
    originals = [np.full((24, 36, 3), value, np.uint8) for value in (50, 150, 230)]
    sources = []
    for index, (image, extension) in enumerate(zip(originals, (".png", ".jpg", ".PNG"), strict=True)):
        path = tmp_path / str(index) / f"01{extension}"
        path.parent.mkdir()
        PagePipeline._write_image(path, image)
        sources.append(path)
    pipeline = PagePipeline(detector=_EmptyOCR(), ocr=_EmptyOCR(), translator=_NeverUsed(),
                            inpainter=_NeverUsed(), renderer=_NeverUsed())
    monkeypatch.setattr(app, "build_pipeline", lambda **kwargs: pipeline)
    output = tmp_path / "out"
    batch = app.create_batch_task(sources, output)
    asyncio.run(app.build_task_queue(PipelineOptions(translate=False)).run(batch.task_id))

    assert app.database.get_task(batch.task_id).status is TaskStatus.COMPLETED
    pages = app.database.list_task_pages(batch.task_id)
    assert len({page.result_path for page in pages}) == 3
    assert len(list((output / "metadata").glob("*.json"))) == 3
    with zipfile.ZipFile(output / "originals.zip") as archive:
        assert len(archive.namelist()) == 3
        for page, image in zip(pages, originals, strict=True):
            assert archive.read(Path(page.source_path).name) == Path(page.source_path).read_bytes()
            metadata = output / "metadata" / f"{Path(page.source_path).stem}.json"
            assert json.loads(metadata.read_text(encoding="utf-8"))["source_path"] == page.source_path
            restored, available = app.load_page_source(metadata)
            assert available and np.array_equal(restored, image)
            assert np.array_equal(PagePipeline._read_image(Path(page.result_path)), image)
    export = app.export_task_zip(batch.task_id, tmp_path / "translated.zip")
    with zipfile.ZipFile(export) as archive:
        assert len(set(archive.namelist())) == 3
        for name, image in zip(archive.namelist(), originals, strict=True):
            decoded = cv2.imdecode(np.frombuffer(archive.read(name), np.uint8), cv2.IMREAD_COLOR)
            assert np.array_equal(decoded, image)
    app.database.close()


def _queue_task(tmp_path, count):
    database = Database(tmp_path / "queue.db")
    database.initialize()
    task = database.create_task_with_pages("chapter", "out", [(index, str(index)) for index in range(count)])
    return database, task


def test_queue_only_allocates_workers_up_to_page_concurrency(tmp_path, monkeypatch):
    database, task = _queue_task(tmp_path, 60)
    create_task = asyncio.create_task
    jobs = []
    completed = []
    active = 0
    peak = 0

    def track(coro, **kwargs):
        job = create_task(coro, **kwargs)
        jobs.append(job)
        return job

    async def process(page, _output, progress):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        completed.append(page.page_index)

    monkeypatch.setattr(asyncio, "create_task", track)
    asyncio.run(PersistentTaskQueue(database, process, max_concurrent_pages=3).run(task))

    assert len(jobs) == 3
    assert peak == 3
    assert sorted(completed) == list(range(60))
    assert database.get_task(task).status is TaskStatus.COMPLETED
    database.close()


@pytest.mark.parametrize("action", ["cancel", "stop"])
def test_bounded_workers_stop_backlog_without_starting_or_losing_pages(tmp_path, action):
    database, task = _queue_task(tmp_path, 11)
    processed = []
    queue = None

    async def process(page, _output, progress):
        processed.append(page.page_index)
        getattr(queue, action)(task)
        await progress.checkpoint()

    queue = PersistentTaskQueue(database, process, max_concurrent_pages=2)
    asyncio.run(queue.run(task))
    pages = database.list_task_pages(task)
    expected = PageStatus.CANCELLED if action == "cancel" else PageStatus.PENDING
    assert processed == [0]
    assert len(pages) == 11 and all(page.status is expected for page in pages)
    assert [page.attempt_count for page in pages] == [1] + [0] * 10
    assert database.get_task(task).status is (TaskStatus.CANCELLED if action == "cancel" else TaskStatus.PAUSED)

    if action == "cancel":
        database.reset_cancelled_pages(task)
    resumed = []

    async def complete(page, _output, progress):
        resumed.append(page.page_index)
        await asyncio.sleep(0)

    asyncio.run(PersistentTaskQueue(database, complete, max_concurrent_pages=2).run(task))
    assert sorted(resumed) == list(range(11))
    assert database.get_task(task).status is TaskStatus.COMPLETED
    database.close()


def test_worker_error_interrupts_running_pages_and_keeps_backlog_resumable(tmp_path):
    database, task = _queue_task(tmp_path, 8)

    async def process(page, _output, progress):
        await asyncio.sleep(0)

    def callback(_task, page):
        raise RuntimeError("callback failure")

    queue = PersistentTaskQueue(database, process, max_concurrent_pages=2, on_page_completed=callback)
    with pytest.raises(RuntimeError, match="callback failure"):
        asyncio.run(queue.run(task))
    assert getattr(database._local, "connection", None) is None
    pages = database.list_task_pages(task)
    assert len(pages) == 8
    assert all(page.status in {PageStatus.COMPLETED, PageStatus.PENDING} for page in pages)
    assert database.get_task(task).status is TaskStatus.PAUSED
    database.close()


def test_external_cancellation_closes_all_workers_without_claiming_backlog(tmp_path):
    database, task = _queue_task(tmp_path, 12)
    entered = []
    closed = []

    async def scenario():
        started = asyncio.Event()

        async def process(page, _output, progress):
            entered.append(page.page_index)
            if len(entered) == 2:
                started.set()
            await asyncio.Event().wait()

        async def close():
            closed.append(True)

        queue = PersistentTaskQueue(database, process, max_concurrent_pages=2, on_run_finished=close)
        running = asyncio.create_task(queue.run(task))
        await asyncio.wait_for(started.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())
    assert closed == [True]
    assert getattr(database._local, "connection", None) is None
    assert entered == [0, 1]
    pages = database.list_task_pages(task)
    assert all(page.status is PageStatus.PENDING for page in pages)
    assert [page.attempt_count for page in pages] == [1, 1] + [0] * 10
    assert database.get_task(task).status is TaskStatus.PAUSED
    database.close()
