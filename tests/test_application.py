import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.domain.enums import PageStatus, TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database
from app.tasks.pipeline import PipelineOptions

from test_application_services import MemoryCredentialBackend


class FakePipeline:
    async def process(self, source_path, output_dir, options, *, on_progress=None):
        on_progress("OCR", 30)
        on_progress("保存", 100)
        return SimpleNamespace(artifacts=SimpleNamespace(translated=Path(output_dir) / "translated.png"))


def _application(tmp_path: Path) -> TranslationApplication:
    paths = AppPaths(
        project_root=tmp_path / "project",
        output_root=tmp_path / "results",
        temp_root=tmp_path / "temp",
    )
    paths.ensure_runtime_directories()
    database = Database(paths.database_path)
    database.initialize()
    return TranslationApplication(
        paths,
        database=database,
        credentials=CredentialStore(MemoryCredentialBackend()),
    )


def test_api_config_keeps_secret_out_of_sqlite(tmp_path: Path) -> None:
    application = _application(tmp_path)

    application.save_api_config(
        {
            "service_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "secret-key",
            "timeout_seconds": 60,
            "retries": 2,
        }
    )

    assert application.credentials.load(application.CHANNEL_ID) == "secret-key"
    database_bytes = application.database.path.read_bytes()
    assert b"secret-key" not in database_bytes
    loaded = application.load_api_config()
    assert loaded["has_api_key"] is True
    assert "api_key" not in loaded


def test_process_page_updates_sqlite_checkpoints(tmp_path: Path) -> None:
    application = _application(tmp_path)
    source = tmp_path / "page.png"
    source.write_bytes(b"placeholder")

    outcome = asyncio.run(
        application.process_page(
            source,
            tmp_path / "out",
            PipelineOptions(),
            pipeline=FakePipeline(),
        )
    )

    assert application.database.get_task(outcome.task_id).status is TaskStatus.COMPLETED
    page = application.database.get_page(outcome.page_id)
    assert page.status is PageStatus.COMPLETED
    assert page.stage == "保存"
    assert page.progress == 100


def test_application_startup_repairs_completed_task_left_running(tmp_path: Path) -> None:
    application = _application(tmp_path)
    task_id = application.database.create_task("page.png", "out")
    page_id = application.database.add_page(task_id, 0, "page.png")
    application.database.update_page_status(
        page_id, PageStatus.COMPLETED, stage="保存", progress=100
    )
    application.database.update_task_status(task_id, TaskStatus.RUNNING)

    application.initialize()

    assert application.database.get_task(task_id).status is TaskStatus.COMPLETED
