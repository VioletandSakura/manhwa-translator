from __future__ import annotations

from pathlib import Path

from app.domain.models import ApiChannelRecord, LibraryEntry
from app.storage.database import Database


def test_api_channels_are_persisted_without_secret_and_sorted_by_priority(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    slower = ApiChannelRecord(
        id="backup",
        service_name="备用",
        enabled=True,
        base_url="https://example.test/v1",
        model="model-b",
        timeout_seconds=30,
        max_concurrency=1,
        rpm_limit=10,
        retries=1,
        priority=20,
        strategy="priority",
        verified=False,
        last_error=None,
    )
    primary = ApiChannelRecord(
        id="deepseek",
        service_name="DeepSeek",
        enabled=True,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        timeout_seconds=60,
        max_concurrency=2,
        rpm_limit=60,
        retries=2,
        priority=10,
        strategy="round_robin",
        verified=True,
        last_error=None,
    )

    database.upsert_api_channel(slower)
    database.upsert_api_channel(primary)

    assert [channel.id for channel in database.list_api_channels()] == ["deepseek", "backup"]
    assert b"api_key" not in database.path.read_bytes().lower()


def test_library_entries_support_glossary_prompts_rules_and_presets(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    entries = [
        LibraryEntry("g1", "glossary", "勇者", '{"target":"勇者"}', True, 10),
        LibraryEntry("p1", "prompt", "默认漫画翻译", '{"system":"忠实翻译"}', True, 10),
        LibraryEntry("r1", "replacement", "省略号", '{"pattern":"...","replacement":"……"}', True, 10),
        LibraryEntry("s1", "preset", "日漫中文", '{"ocr":"mangaocr"}', True, 10),
    ]
    for entry in entries:
        database.upsert_library_entry(entry)

    assert [item.name for item in database.list_library_entries("glossary")] == ["勇者"]
    assert database.get_library_entry("p1").category == "prompt"
    database.delete_library_entry("r1")
    assert database.list_library_entries("replacement") == []
