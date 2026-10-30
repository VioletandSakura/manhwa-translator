from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock

from app.security.redaction import redact_text


class SafeLogger:
    """线程安全的本地日志器，写入前统一脱敏。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def info(self, message: str) -> None:
        self._write("信息", message)

    def warning(self, message: str) -> None:
        self._write("警告", message)

    def error(self, message: str) -> None:
        self._write("错误", message)

    def _write(self, level: str, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"{timestamp} [{level}] {redact_text(message)}\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
