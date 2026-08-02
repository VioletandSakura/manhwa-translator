from __future__ import annotations

from pathlib import Path

import pytest

from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database

from test_application_services import MemoryCredentialBackend


def _application(tmp_path: Path) -> TranslationApplication:
    paths = AppPaths(tmp_path / "project", tmp_path / "results", tmp_path / "temp")
    app = TranslationApplication(paths, database=Database(paths.database_path), credentials=CredentialStore(MemoryCredentialBackend()))
    app.initialize()
    return app


def test_application_manages_multiple_api_channels_and_separate_keys(tmp_path: Path) -> None:
    application = _application(tmp_path)
    application.save_api_channel(
        {
            "id": "backup",
            "service_name": "备用通道",
            "enabled": True,
            "base_url": "https://example.test/v1",
            "model": "backup-model",
            "api_key": "backup-secret",
            "timeout_seconds": 45,
            "max_concurrency": 2,
            "rpm_limit": 30,
            "retries": 1,
            "priority": 20,
            "strategy": "round_robin",
        }
    )

    channel = application.database.get_api_channel("backup")
    assert channel.max_concurrency == 2
    assert channel.rpm_limit == 30
    assert application.credentials.load("backup") == "backup-secret"
    application.delete_api_channel("backup")
    assert application.database.list_api_channels() == []
    assert application.credentials.load("backup") is None


def test_new_api_channel_is_not_reported_saved_without_persisted_key(tmp_path: Path) -> None:
    application = _application(tmp_path)

    with pytest.raises(ValueError, match="请输入并保存API Key"):
        application.save_api_channel(
            {
                "id": "missing-key",
                "service_name": "DeepSeek",
                "enabled": True,
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
            }
        )

    assert application.database.list_api_channels() == []


def test_existing_channel_can_update_settings_without_reentering_key(tmp_path: Path) -> None:
    application = _application(tmp_path)
    values = {
        "id": "deepseek-default",
        "service_name": "DeepSeek",
        "enabled": True,
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "api_key": "stored-secret",
    }
    application.save_api_channel(values)

    application.save_api_channel({**values, "api_key": "", "rpm_limit": 30})

    assert application.database.get_api_channel("deepseek-default").rpm_limit == 30
    assert application.credentials.load("deepseek-default") == "stored-secret"


def test_application_saves_and_loads_named_local_content(tmp_path: Path) -> None:
    application = _application(tmp_path)
    entry_id = application.save_library_content("glossary", "常用术语", "勇者\t勇者")

    entries = application.list_library_content("glossary")
    assert entries[0].id == entry_id
    assert entries[0].payload == "勇者\t勇者"
