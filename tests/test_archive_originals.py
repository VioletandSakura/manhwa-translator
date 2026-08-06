from __future__ import annotations

import zipfile
from pathlib import Path

from app.domain.enums import PageStatus, TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database

from test_application_services import MemoryCredentialBackend


def _application(tmp_path: Path) -> TranslationApplication:
    paths = AppPaths(tmp_path / "project", tmp_path / "results", tmp_path / "temp")
    application = TranslationApplication(
        paths,
        database=Database(paths.database_path),
        credentials=CredentialStore(MemoryCredentialBackend()),
    )
    application.initialize()
    return application


def _completed_task_with_originals(
    tmp_path: Path,
) -> tuple[TranslationApplication, str, Path]:
    application = _application(tmp_path)
    output = tmp_path / "out"
    original_dir = output / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "1.png").write_bytes(b"one")
    (original_dir / "2.png").write_bytes(b"two")
    task_id = application.database.create_task("src", str(output))
    application.database.update_task_status(task_id, TaskStatus.COMPLETED)
    return application, task_id, output


def test_archive_originals_zips_and_removes_loose_files(tmp_path: Path) -> None:
    application, task_id, output = _completed_task_with_originals(tmp_path)

    archive_path = application.archive_originals(task_id)

    assert archive_path == output / "originals.zip"
    with zipfile.ZipFile(archive_path) as archive:
        assert sorted(archive.namelist()) == ["1.png", "2.png"]
        assert archive.read("1.png") == b"one"
    assert not (output / "original").exists()


def test_archive_skips_task_that_is_not_completed(tmp_path: Path) -> None:
    application = _application(tmp_path)
    output = tmp_path / "out"
    original_dir = output / "original"
    original_dir.mkdir(parents=True)
    source = original_dir / "1.png"
    source.write_bytes(b"one")
    task_id = application.database.create_task("src", str(output))

    archive_path = application.archive_originals(task_id)

    assert archive_path is None
    assert source.is_file()
    assert not (output / "originals.zip").exists()


def test_archive_skips_task_with_failed_pages(tmp_path: Path) -> None:
    application, task_id, output = _completed_task_with_originals(tmp_path)
    page_id = application.database.add_page(task_id, 0, "page.png")
    application.database.update_page_status(
        page_id, PageStatus.FAILED,
        stage="失败",
        progress=0,
    )
    application.database.update_task_status(task_id, TaskStatus.FAILED)

    archive_path = application.archive_originals(task_id)

    assert archive_path is None
    assert (output / "original" / "1.png").is_file()
    assert not (output / "originals.zip").exists()


def test_archive_skips_task_with_cancelled_pages(tmp_path: Path) -> None:
    application, task_id, output = _completed_task_with_originals(tmp_path)
    page_id = application.database.add_page(task_id, 0, "page.png")
    application.database.update_page_status(
        page_id, PageStatus.CANCELLED,
        stage="已取消",
        progress=0,
    )
    application.database.update_task_status(task_id, TaskStatus.CANCELLED)

    archive_path = application.archive_originals(task_id)

    assert archive_path is None
    assert (output / "original" / "1.png").is_file()
    assert not (output / "originals.zip").exists()


def test_archive_is_idempotent_when_zip_already_exists(tmp_path: Path) -> None:
    application, task_id, output = _completed_task_with_originals(tmp_path)
    archive_path = application.archive_originals(task_id)

    second = application.archive_originals(task_id)

    assert second == archive_path
    assert not (output / "original").exists()


def test_archive_skips_when_original_folder_is_missing(tmp_path: Path) -> None:
    application = _application(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    task_id = application.database.create_task("src", str(output))
    application.database.update_task_status(task_id, TaskStatus.COMPLETED)

    archive_path = application.archive_originals(task_id)

    assert archive_path is None


def test_queue_does_not_archive_when_any_page_fails(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.tasks.queue import PersistentTaskQueue

    application = _application(tmp_path)
    output = tmp_path / "out"
    original_dir = output / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "1.png").write_bytes(b"one")
    (original_dir / "2.png").write_bytes(b"two")
    task_id = application.database.create_task("src", str(output))
    application.database.add_page(task_id, 0, "1.png")
    application.database.add_page(task_id, 1, "2.png")

    async def process(page, _output_dir, on_progress):
        if page.page_index == 1:
            raise RuntimeError("boom")
        return SimpleNamespace(artifacts=SimpleNamespace(translated=None))

    queue = PersistentTaskQueue(
        application.database,
        process,
        on_task_completed=application.archive_originals,
    )

    asyncio.run(queue.run(task_id))

    assert application.database.get_task(task_id).status is TaskStatus.FAILED
    assert (original_dir / "1.png").is_file()
    assert (original_dir / "2.png").is_file()
    assert not (output / "originals.zip").exists()
