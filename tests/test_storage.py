from pathlib import Path

from app.domain.enums import PageStatus, TaskStatus
from app.storage.database import Database


def test_database_persists_task_and_page_checkpoint(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id = database.create_task(source_path="page.png", output_dir="out")
    page_id = database.add_page(task_id=task_id, page_index=0, source_path="page.png")

    database.update_task_status(task_id, TaskStatus.RUNNING)
    database.update_page_status(page_id, PageStatus.COMPLETED, stage="保存", progress=100)

    task = database.get_task(task_id)
    page = database.get_page(page_id)
    assert task.status is TaskStatus.RUNNING
    assert page.status is PageStatus.COMPLETED
    assert page.stage == "保存"
    assert page.progress == 100


def test_database_returns_incomplete_pages_for_resume(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id = database.create_task(source_path="chapter", output_dir="out")
    first = database.add_page(task_id, 0, "001.png")
    second = database.add_page(task_id, 1, "002.png")
    database.update_page_status(first, PageStatus.COMPLETED, stage="保存", progress=100)

    remaining = database.list_resumable_pages(task_id)

    assert [page.id for page in remaining] == [second]


def test_database_repairs_running_task_when_all_pages_are_completed(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id = database.create_task(source_path="page.png", output_dir="out")
    page_id = database.add_page(task_id, 0, "page.png")
    database.update_page_status(page_id, PageStatus.COMPLETED, stage="保存", progress=100)
    database.update_task_status(task_id, TaskStatus.RUNNING)

    repaired = database.repair_completed_task_statuses()

    assert repaired == 1
    assert database.get_task(task_id).status is TaskStatus.COMPLETED


def test_translation_cache_uses_provider_model_and_languages(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.put_translation_cache(
        provider="DeepSeek",
        model="deepseek-chat",
        source_language="auto",
        target_language="zh-Hans",
        source_text="こんにちは",
        translated_text="你好",
    )

    assert database.get_translation_cache(
        provider="DeepSeek",
        model="deepseek-chat",
        source_language="auto",
        target_language="zh-Hans",
        source_text="こんにちは",
    ) == "你好"


def test_database_reuses_connection_per_thread_and_close_resets(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()

    first = database.connect()
    assert database.connect() is first

    database.close()

    assert database.connect() is not first


def test_translation_cache_batch_read_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.put_translation_cache_many(
        provider="DeepSeek",
        model="deepseek-chat",
        source_language="auto",
        target_language="zh-Hans",
        entries={"hello": "\u4f60\u597d", "world": "\u4e16\u754c"},
    )

    found = database.get_translation_cache_many(
        provider="DeepSeek",
        model="deepseek-chat",
        source_language="auto",
        target_language="zh-Hans",
        texts=["hello", "missing", "world"],
    )

    assert found == {"hello": "\u4f60\u597d", "world": "\u4e16\u754c"}
    assert database.get_translation_cache_many(
        provider="DeepSeek",
        model="deepseek-chat",
        source_language="auto",
        target_language="zh-Hans",
        texts=[],
    ) == {}
