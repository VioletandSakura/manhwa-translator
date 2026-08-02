from __future__ import annotations

from pathlib import Path
import zipfile

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


def test_application_creates_one_persistent_task_for_imported_chapter(tmp_path: Path) -> None:
    application = _application(tmp_path)
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    (chapter / "2.png").write_bytes(b"two")
    (chapter / "1.png").write_bytes(b"one")

    batch = application.create_batch_task([chapter], tmp_path / "out")

    pages = application.database.list_task_pages(batch.task_id)
    assert [Path(page.source_path).name for page in pages] == ["1.png", "2.png"]
    assert batch.page_count == 2


def test_saving_legacy_api_form_also_creates_default_channel(tmp_path: Path) -> None:
    application = _application(tmp_path)

    application.save_api_config(
        {
            "service_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "local-secret",
            "timeout_seconds": 60,
            "retries": 2,
        }
    )

    channel = application.database.get_api_channel(application.CHANNEL_ID)
    assert channel.service_name == "DeepSeek"
    assert channel.verified is False
    assert application.credentials.load(channel.id) == "local-secret"


def test_deepseek_model_id_is_normalized_when_channel_is_saved(tmp_path: Path) -> None:
    application = _application(tmp_path)

    application.save_api_channel(
        {
            "id": "deepseek-default",
            "service_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "DeepSeek-V4-Flash",
            "api_key": "local-secret",
            "timeout_seconds": 60,
            "retries": 4,
        }
    )

    assert application.database.get_api_channel("deepseek-default").model == "deepseek-v4-flash"


def test_application_exports_completed_pages_to_zip_with_original_names(tmp_path: Path) -> None:
    application = _application(tmp_path)
    task_id = application.database.create_task("chapter", str(tmp_path / "out"))
    page_id = application.database.add_page(task_id, 0, str(tmp_path / "001.png"))
    translated = tmp_path / "out" / "translated" / "001_translated.png"
    translated.parent.mkdir(parents=True)
    translated.write_bytes(b"translated")
    application.database.set_page_result(page_id, str(translated))
    target = tmp_path / "result.zip"

    application.export_task_zip(task_id, target)

    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["001.png"]
        assert archive.read("001.png") == b"translated"
