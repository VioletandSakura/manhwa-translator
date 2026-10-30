from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


def _digest(stream) -> bytes:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.digest()


def verify_archive(archive_path: Path, names: set[str], remaining: list[Path]) -> None:
    """完整读取会校验 CRC；逐字节摘要核对仍在磁盘上的副本。"""
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) != len(names) or {entry.filename for entry in entries} != names:
            raise ValueError("原图归档的文件清单与任务不一致，保留所有原图副本")
        digests = {}
        for entry in entries:
            with archive.open(entry) as stream:
                digests[entry.filename] = _digest(stream)
        for path in remaining:
            with path.open("rb") as stream:
                if path.name not in digests or _digest(stream) != digests[path.name]:
                    raise ValueError(f"原图归档内容不一致：{path.name}，保留副本")


def archive_originals(output_dir: Path, names: set[str]) -> Path | None:
    original_dir = output_dir / "original"
    archive_path = output_dir / "originals.zip"
    files = sorted((path for path in original_dir.iterdir() if path.is_file()), key=lambda p: p.name) if original_dir.is_dir() else []
    if not names:
        return None
    if archive_path.exists():
        # Also handles a previous process dying partway through unlinking copies.
        verify_archive(archive_path, names, files)
    else:
        if {path.name for path in files} != names:
            raise ValueError("原图副本不完整，无法安全归档")
        descriptor, temporary = tempfile.mkstemp(prefix=".originals-", suffix=".tmp", dir=output_dir)
        os.close(descriptor)
        temporary = Path(temporary)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            verify_archive(temporary, names, files)
            os.replace(temporary, archive_path)
        finally:
            temporary.unlink(missing_ok=True)
    # Every original is already present in a fully checked archive. A crash
    # during these deletes is safe; the next invocation validates the remainder.
    for path in files:
        path.unlink()
    if original_dir.is_dir() and not any(original_dir.iterdir()):
        original_dir.rmdir()
    return archive_path
