from __future__ import annotations

import os
from pathlib import Path
import tempfile


def write_bytes(path: Path, content: bytes) -> None:
    """同目录写完并同步后再替换，避免中断把已有工件截成半个文件。"""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
