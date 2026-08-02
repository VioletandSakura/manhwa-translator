import asyncio
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.detection.opencv_detector import OpenCVTextDetector
from app.domain.models import TextRegion
from app.inpainting.opencv_inpainter import OpenCVInpainter
from app.rendering.pillow_renderer import PillowTextRenderer
from app.tasks.pipeline import PagePipeline, PipelineOptions


class FakeDetector:
    async def detect(self, image: np.ndarray) -> list[TextRegion]:
        return [
            TextRegion(
                id="region-1",
                polygon=[(20, 20), (180, 20), (180, 80), (20, 80)],
                direction="horizontal",
            )
        ]


class FakeOCR:
    async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]:
        regions[0].source_text = "Hello"
        regions[0].confidence = 0.95
        return regions


class FakeTranslator:
    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        assert source_language == "auto"
        assert target_language == "zh-Hans"
        return ["你好"]


def _make_page(path: Path) -> None:
    image = Image.new("RGB", (220, 120), "white")
    image.save(path)


def test_page_pipeline_saves_separate_intermediate_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "001.png"
    _make_page(source)
    progress: list[tuple[str, int]] = []
    pipeline = PagePipeline(
        detector=FakeDetector(),
        ocr=FakeOCR(),
        translator=FakeTranslator(),
        inpainter=OpenCVInpainter(),
        renderer=PillowTextRenderer(),
    )

    result = asyncio.run(
        pipeline.process(
            source,
            tmp_path / "output",
            PipelineOptions(),
            on_progress=lambda stage, value: progress.append((stage, value)),
        )
    )

    assert result.artifacts.original.exists()
    assert result.artifacts.inpainted.exists()
    assert result.artifacts.translated.exists()
    assert result.artifacts.metadata.exists()
    assert Image.open(result.artifacts.translated).size == (220, 120)
    metadata = json.loads(result.artifacts.metadata.read_text(encoding="utf-8"))
    assert metadata["regions"][0]["source_text"] == "Hello"
    assert metadata["regions"][0]["translated_text"] == "你好"
    assert progress[-1] == ("保存", 100)


def test_low_ocr_confidence_is_marked_for_review(tmp_path: Path) -> None:
    class LowConfidenceOCR(FakeOCR):
        async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]:
            regions = await super().recognize(image, regions)
            regions[0].confidence = 0.2
            return regions

    source = tmp_path / "page.png"
    _make_page(source)
    pipeline = PagePipeline(
        detector=FakeDetector(),
        ocr=LowConfidenceOCR(),
        translator=FakeTranslator(),
        inpainter=OpenCVInpainter(),
        renderer=PillowTextRenderer(),
    )

    result = asyncio.run(pipeline.process(source, tmp_path / "out", PipelineOptions(review_threshold=0.6)))

    assert result.regions[0].needs_review is True


def test_opencv_detector_finds_dark_text_cluster() -> None:
    image = np.full((200, 300, 3), 255, dtype=np.uint8)
    cv2.putText(image, "TEST", (40, 110), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 5)

    regions = asyncio.run(OpenCVTextDetector(min_area=50).detect(image))

    assert regions
    assert all(len(region.polygon) == 4 for region in regions)


def test_inpainter_and_renderer_preserve_resolution() -> None:
    image = np.full((120, 220, 3), 255, dtype=np.uint8)
    image[30:70, 30:170] = 0
    region = TextRegion(
        id="one",
        polygon=[(25, 25), (175, 25), (175, 75), (25, 75)],
        translated_text="测试文字",
    )

    cleaned = asyncio.run(OpenCVInpainter().inpaint(image, [region]))
    rendered = asyncio.run(PillowTextRenderer().render(cleaned, [region]))

    assert cleaned.shape == image.shape
    assert rendered.shape == image.shape
    assert np.any(rendered != cleaned)

