from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.domain.enums import PageStatus, TaskStatus
from app.domain.models import ApiChannelRecord, LibraryEntry, PageRecord, TaskRecord


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """SQLite 持久化层：每线程复用连接，WAL 模式，含任务与翻译缓存。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        """返回当前线程复用的 SQLite 连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            self._local.connection = connection
        return connection

    def close(self) -> None:
        """关闭当前线程持有的连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def __enter__(self) -> sqlite3.Connection:
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def initialize(self) -> None:
        """建表并应用轻量迁移（含页面任务状态索引）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
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
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    result_path TEXT,
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
                CREATE TABLE IF NOT EXISTS api_channels (
                    id TEXT PRIMARY KEY,
                    service_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL DEFAULT 60,
                    max_concurrency INTEGER NOT NULL DEFAULT 1,
                    rpm_limit INTEGER NOT NULL DEFAULT 60,
                    retries INTEGER NOT NULL DEFAULT 2,
                    priority INTEGER NOT NULL DEFAULT 100,
                    strategy TEXT NOT NULL DEFAULT 'priority',
                    verified INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_entries (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, name)
                );
                CREATE INDEX IF NOT EXISTS idx_pages_task_status
                    ON pages(task_id, status);
                """
            )
            self._ensure_column(connection, "pages", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "pages", "result_path", "TEXT")
            connection.commit()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_api_channel(self, channel: ApiChannelRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO api_channels(
                    id, service_name, enabled, base_url, model, timeout_seconds,
                    max_concurrency, rpm_limit, retries, priority, strategy,
                    verified, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    service_name=excluded.service_name, enabled=excluded.enabled,
                    base_url=excluded.base_url, model=excluded.model,
                    timeout_seconds=excluded.timeout_seconds,
                    max_concurrency=excluded.max_concurrency, rpm_limit=excluded.rpm_limit,
                    retries=excluded.retries, priority=excluded.priority,
                    strategy=excluded.strategy, verified=excluded.verified,
                    last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (
                    channel.id, channel.service_name, int(channel.enabled), channel.base_url,
                    channel.model, channel.timeout_seconds, channel.max_concurrency,
                    channel.rpm_limit, channel.retries, channel.priority, channel.strategy,
                    int(channel.verified), channel.last_error, _now(),
                ),
            )
            connection.commit()

    def list_api_channels(self, *, enabled_only: bool = False) -> list[ApiChannelRecord]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM api_channels {where} ORDER BY priority, service_name, id").fetchall()
        return [self._to_api_channel(row) for row in rows]

    def get_api_channel(self, channel_id: str) -> ApiChannelRecord:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM api_channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None:
            raise KeyError(f"API通道不存在：{channel_id}")
        return self._to_api_channel(row)

    def delete_api_channel(self, channel_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM api_channels WHERE id = ?", (channel_id,))
            connection.commit()

    def set_api_channel_health(self, channel_id: str, *, verified: bool, last_error: str | None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE api_channels SET verified = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (int(verified), last_error, _now(), channel_id),
            )
            connection.commit()

    def upsert_library_entry(self, entry: LibraryEntry) -> None:
        if entry.category not in {"glossary", "prompt", "replacement", "preset"}:
            raise ValueError(f"不支持的资料类别：{entry.category}")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO library_entries(id, category, name, payload, enabled, sort_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    category=excluded.category, name=excluded.name, payload=excluded.payload,
                    enabled=excluded.enabled, sort_order=excluded.sort_order, updated_at=excluded.updated_at""",
                (entry.id, entry.category, entry.name, entry.payload, int(entry.enabled), entry.sort_order, _now()),
            )
            connection.commit()

    def list_library_entries(self, category: str) -> list[LibraryEntry]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM library_entries WHERE category = ? ORDER BY sort_order, name, id", (category,)
            ).fetchall()
        return [self._to_library_entry(row) for row in rows]

    def get_library_entry(self, entry_id: str) -> LibraryEntry:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM library_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"资料条目不存在：{entry_id}")
        return self._to_library_entry(row)

    def delete_library_entry(self, entry_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM library_entries WHERE id = ?", (entry_id,))
            connection.commit()

    def create_task(self, source_path: str, output_dir: str) -> str:
        task_id = uuid.uuid4().hex
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, source_path, output_dir, TaskStatus.PENDING.value, timestamp, timestamp),
            )
            connection.commit()
        return task_id

    def add_page(self, task_id: str, page_index: int, source_path: str) -> str:
        page_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pages
                (id, task_id, page_index, source_path, status, stage, progress)
                VALUES (?, ?, ?, ?, ?, '', 0)""",
                (page_id, task_id, page_index, source_path, PageStatus.PENDING.value),
            )
            connection.commit()
        return page_id

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), task_id),
            )
            connection.commit()

    def repair_completed_task_statuses(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE tasks
                SET status = ?, updated_at = ?
                WHERE status != ?
                  AND EXISTS (SELECT 1 FROM pages WHERE pages.task_id = tasks.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM pages
                      WHERE pages.task_id = tasks.id AND pages.status != ?
                  )""",
                (
                    TaskStatus.COMPLETED.value,
                    _now(),
                    TaskStatus.COMPLETED.value,
                    PageStatus.COMPLETED.value,
                ),
            )
            connection.commit()
            return max(0, cursor.rowcount)

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
        with self.connect() as connection:
            connection.execute(
                "UPDATE pages SET status = ?, stage = ?, progress = ?, error = ? WHERE id = ?",
                (status.value, stage, progress, error, page_id),
            )
            connection.commit()

    def increment_page_attempt(self, page_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE pages SET attempt_count = attempt_count + 1 WHERE id = ?", (page_id,))
            connection.commit()

    def reset_page_for_retry(self, page_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE pages SET status = ?, stage = '', progress = 0, error = NULL WHERE id = ?",
                (PageStatus.PENDING.value, page_id),
            )
            connection.commit()

    def set_page_result(self, page_id: str, result_path: str | None) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE pages SET result_path = ? WHERE id = ?", (result_path, page_id))
            connection.commit()

    def get_task(self, task_id: str) -> TaskRecord:
        with self.connect() as connection:
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

    def list_tasks(self, limit: int = 200) -> list[TaskRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [
            TaskRecord(
                id=row["id"], source_path=row["source_path"], output_dir=row["output_dir"],
                status=TaskStatus(row["status"]), created_at=row["created_at"], updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_page(self, page_id: str) -> PageRecord:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if row is None:
            raise KeyError(f"页面不存在: {page_id}")
        return self._to_page(row)

    def list_resumable_pages(self, task_id: str) -> list[PageRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM pages
                WHERE task_id = ? AND status IN (?, ?, ?) ORDER BY page_index""",
                (task_id, PageStatus.PENDING.value, PageStatus.RUNNING.value, PageStatus.FAILED.value),
            ).fetchall()
        return [self._to_page(row) for row in rows]

    def list_task_pages(self, task_id: str) -> list[PageRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pages WHERE task_id = ? ORDER BY page_index", (task_id,)
            ).fetchall()
        return [self._to_page(row) for row in rows]

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, _now()),
            )
            connection.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as connection:
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
        with self.connect() as connection:
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
        with self.connect() as connection:
            row = connection.execute(
                "SELECT translated_text FROM translation_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return None if row is None else str(row["translated_text"])

    def get_translation_cache_many(
        self,
        *,
        provider: str,
        model: str,
        source_language: str,
        target_language: str,
        texts: list[str],
    ) -> dict[str, str]:
        if not texts:
            return {}
        keys = {
            text: self._cache_key(provider, model, source_language, target_language, text)
            for text in texts
        }
        placeholders = ",".join("?" for _ in keys)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT cache_key, translated_text FROM translation_cache "
                f"WHERE cache_key IN ({placeholders})",
                tuple(keys.values()),
            ).fetchall()
        found = {str(row["cache_key"]): str(row["translated_text"]) for row in rows}
        return {text: found[key] for text, key in keys.items() if key in found}

    def put_translation_cache_many(
        self,
        *,
        provider: str,
        model: str,
        source_language: str,
        target_language: str,
        entries: dict[str, str],
    ) -> None:
        if not entries:
            return
        timestamp = _now()
        rows = [
            (
                self._cache_key(provider, model, source_language, target_language, text),
                translated,
                timestamp,
            )
            for text, translated in entries.items()
        ]
        with self.connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO translation_cache VALUES (?, ?, ?)", rows
            )
            connection.commit()

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
            attempt_count=row["attempt_count"],
            result_path=row["result_path"],
        )

    @staticmethod
    def _to_api_channel(row: sqlite3.Row) -> ApiChannelRecord:
        return ApiChannelRecord(
            id=row["id"], service_name=row["service_name"], enabled=bool(row["enabled"]),
            base_url=row["base_url"], model=row["model"], timeout_seconds=row["timeout_seconds"],
            max_concurrency=row["max_concurrency"], rpm_limit=row["rpm_limit"],
            retries=row["retries"], priority=row["priority"], strategy=row["strategy"],
            verified=bool(row["verified"]), last_error=row["last_error"],
        )

    @staticmethod
    def _to_library_entry(row: sqlite3.Row) -> LibraryEntry:
        return LibraryEntry(
            id=row["id"], category=row["category"], name=row["name"], payload=row["payload"],
            enabled=bool(row["enabled"]), sort_order=row["sort_order"],
        )
