from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.domain.enums import PageStatus, TaskStatus
from app.domain.models import PageRecord, TaskRecord


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    page_index INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
                    error TEXT,
                    UNIQUE(task_id, page_index)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS translation_cache (
                    cache_key TEXT PRIMARY KEY,
                    translated_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def create_task(self, source_path: str, output_dir: str) -> str:
        task_id = uuid.uuid4().hex
        timestamp = _now()
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, source_path, output_dir, TaskStatus.PENDING.value, timestamp, timestamp),
            )
            connection.commit()
        return task_id

    def add_page(self, task_id: str, page_index: int, source_path: str) -> str:
        page_id = uuid.uuid4().hex
        with closing(self.connect()) as connection:
            connection.execute(
                """INSERT INTO pages
                (id, task_id, page_index, source_path, status, stage, progress)
                VALUES (?, ?, ?, ?, ?, '', 0)""",
                (page_id, task_id, page_index, source_path, PageStatus.PENDING.value),
            )
            connection.commit()
        return page_id

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), task_id),
            )
            connection.commit()

    def update_page_status(
        self,
        page_id: str,
        status: PageStatus,
        *,
        stage: str,
        progress: int,
        error: str | None = None,
    ) -> None:
        if not 0 <= progress <= 100:
            raise ValueError("页面进度必须在0到100之间")
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE pages SET status = ?, stage = ?, progress = ?, error = ? WHERE id = ?",
                (status.value, stage, progress, error, page_id),
            )
            connection.commit()

    def get_task(self, task_id: str) -> TaskRecord:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"任务不存在: {task_id}")
        return TaskRecord(
            id=row["id"],
            source_path=row["source_path"],
            output_dir=row["output_dir"],
            status=TaskStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_page(self, page_id: str) -> PageRecord:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if row is None:
            raise KeyError(f"页面不存在: {page_id}")
        return self._to_page(row)

    def list_resumable_pages(self, task_id: str) -> list[PageRecord]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM pages
                WHERE task_id = ? AND status != ? ORDER BY page_index""",
                (task_id, PageStatus.COMPLETED.value),
            ).fetchall()
        return [self._to_page(row) for row in rows]

    def set_setting(self, key: str, value: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, _now()),
            )
            connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    @staticmethod
    def _cache_key(
        provider: str,
        model: str,
        source_language: str,
        target_language: str,
        source_text: str,
    ) -> str:
        raw = "\x1f".join((provider, model, source_language, target_language, source_text))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def put_translation_cache(
        self,
        *,
        provider: str,
        model: str,
        source_language: str,
        target_language: str,
        source_text: str,
        translated_text: str,
    ) -> None:
        cache_key = self._cache_key(provider, model, source_language, target_language, source_text)
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO translation_cache VALUES (?, ?, ?)",
                (cache_key, translated_text, _now()),
            )
            connection.commit()

    def get_translation_cache(
        self,
        *,
        provider: str,
        model: str,
        source_language: str,
        target_language: str,
        source_text: str,
    ) -> str | None:
        cache_key = self._cache_key(provider, model, source_language, target_language, source_text)
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT translated_text FROM translation_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return None if row is None else str(row["translated_text"])

    @staticmethod
    def _to_page(row: sqlite3.Row) -> PageRecord:
        return PageRecord(
            id=row["id"],
            task_id=row["task_id"],
            page_index=row["page_index"],
            source_path=row["source_path"],
            status=PageStatus(row["status"]),
            stage=row["stage"],
            progress=row["progress"],
            error=row["error"],
        )

