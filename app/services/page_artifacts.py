from __future__ import annotations

import zipfile
from pathlib import Path

import cv2
import numpy as np


def decode_image(data: bytes, description: str, *, grayscale: bool = False) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取图片：{description}")
    return image


def source_image(metadata_path: Path, document: dict) -> tuple[np.ndarray, bool]:
    """Prefer the immutable output copy/archive, so edits do not compound old erasures."""
    output = metadata_path.parent.parent
    original_name = Path(document.get("source_path", "")).name
    if original_name:
        original = output / "original" / original_name
        if original.is_file():
            return decode_image(original.read_bytes(), original.name), True
        archive = output / "originals.zip"
        if archive.is_file():
            try:
                with zipfile.ZipFile(archive) as stream:
                    item = stream.getinfo(original_name)
                    if item.file_size > 256 * 1024 * 1024:
                        raise ValueError("原图归档中的页面过大，无法安全读取。")
                    return decode_image(stream.read(item), original_name), True
            except KeyError:
                pass
            except zipfile.BadZipFile as error:
                raise ValueError("原图归档已损坏，请恢复原图副本后重绘。") from error
        imported = Path(document.get("source_path", ""))
        if imported.is_file() and imported.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return decode_image(imported.read_bytes(), imported.name), True
    legacy = output / "inpainted" / f"{metadata_path.stem}_inpainted.png"
    if not legacy.is_file():
        raise ValueError("找不到原图副本、归档或修复图，无法编辑此页。")
    return decode_image(legacy.read_bytes(), legacy.name), False


def manual_mask(metadata_path: Path, document: dict, shape: tuple[int, ...]) -> np.ndarray:
    name = document.get("editor_state", {}).get("mask_path")
    if not name:
        return np.zeros(shape[:2], dtype=np.uint8)
    path = metadata_path.parent / Path(str(name)).name
    if path.resolve().parent != metadata_path.parent.resolve() or not path.is_file():
        raise ValueError("找不到本页已保存的蒙版，请恢复蒙版文件。")
    mask = decode_image(path.read_bytes(), path.name, grayscale=True)
    if mask.shape != shape[:2]:
        raise ValueError("蒙版尺寸与页面不一致，已保留原文件，请检查后再打开。")
    return np.where(mask > 127, 255, 0).astype(np.uint8)
