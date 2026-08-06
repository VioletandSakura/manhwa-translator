from __future__ import annotations

import zipfile
from pathlib import Path

from app.domain.enums import TaskStatus
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
