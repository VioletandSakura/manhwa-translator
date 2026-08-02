from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from app.domain.models import TextRegion
from app.inpainting.fallback import FallbackInpainter
from app.inpainting.lama_onnx import LaMaONNXInpainter
from app.ocr.mangaocr_engine import MangaOCREngine


def _region() -> TextRegion:
    return TextRegion(id="r1", polygon=[(1, 1), (8, 1), (8, 8), (1, 8)])


def test_mangaocr_recognizes_each_detected_region_with_injected_model() -> None:
    received_sizes: list[tuple[int, int]] = []

    def model(image) -> str:
        received_sizes.append(image.size)
        return "こんにちは"

    engine = MangaOCREngine(model=model)
    regions = asyncio.run(engine.recognize(np.zeros((10, 10, 3), dtype=np.uint8), [_region()]))

    assert regions[0].source_text == "こんにちは"
    assert regions[0].confidence == 1.0
    assert received_sizes == [(7, 7)]


def test_mangaocr_requires_explicit_local_model_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="本地模型"):
        MangaOCREngine(model_path=tmp_path / "missing")


def test_lama_requires_explicit_onnx_model_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="LaMa ONNX"):
        LaMaONNXInpainter(tmp_path / "missing.onnx")


def test_inpainting_falls_back_to_opencv_when_optional_engine_fails() -> None:
    image = np.zeros((8, 8, 3), dtype=np.uint8)

    class Broken:
        async def inpaint(self, image, regions):
            raise RuntimeError("model failed")

    class Working:
        async def inpaint(self, image, regions):
            return np.full_like(image, 123)

    result = asyncio.run(FallbackInpainter(Broken(), Working()).inpaint(image, [_region()]))
    assert int(result[0, 0, 0]) == 123


def test_lama_onnx_supports_two_input_image_and_mask_session(tmp_path: Path) -> None:
    class Input:
        def __init__(self, name: str) -> None:
            self.name = name

    class Session:
        def get_inputs(self):
            return [Input("image"), Input("mask")]

        def run(self, _outputs, feed):
            assert set(feed) == {"image", "mask"}
            _, _, height, width = feed["image"].shape
            return [np.ones((1, 3, height, width), dtype=np.float32)]

    image = np.zeros((10, 10, 3), dtype=np.uint8)
    result = asyncio.run(
        LaMaONNXInpainter(tmp_path / "unused.onnx", session=Session()).inpaint(image, [_region()])
    )

    assert int(result[5, 5, 0]) == 255
    assert int(result[0, 0, 0]) == 0
