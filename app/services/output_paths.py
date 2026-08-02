from __future__ import annotations

import re
from pathlib import Path


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_BATCH_ARCHIVE_EXTENSIONS = {".zip", ".epub"}


def batch_requires_name(sources: list[Path]) -> bool:
    """Return whether the selected input represents a user-visible batch."""
    if len(sources) > 1:
        return True
    return any(source.is_dir() or source.suffix.lower() in _BATCH_ARCHIVE_EXTENSIONS for source in sources)


def resolve_batch_output(output_root: Path, batch_name: str, *, require_name: bool) -> Path:
    """Resolve a safe, direct child output directory for a named batch."""
    root = Path(output_root)
    name = batch_name.strip()
    if not name:
        if require_name:
            raise ValueError("多图、文件夹、ZIP 或 EPUB 任务必须填写批次名称。")
        return root
    if len(name) > 120:
        raise ValueError("批次名称不能超过 120 个字符。")
    if name in {".", ".."} or _INVALID_WINDOWS_CHARS.search(name):
        raise ValueError('批次名称不能包含 \\ / : * ? " < > | 等路径字符。')
    if any(ord(character) < 32 for character in name) or name.endswith((".", " ")):
        raise ValueError("批次名称不能包含控制字符，也不能以句点或空格结尾。")
    if name.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError("该批次名称是 Windows 保留名称，请换一个名称。")

    resolved_root = root.resolve()
    candidate = (resolved_root / name).resolve()
    if candidate.parent != resolved_root:
        raise ValueError("批次名称必须是单层文件夹名称。")
    if candidate.exists():
        if not candidate.is_dir() or any(candidate.iterdir()):
            raise ValueError(f"批次目录“{name}”已经存在且不为空，请换一个名称。")
    return root / name
