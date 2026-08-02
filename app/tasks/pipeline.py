from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2

from app.detection.base import TextDetector
from app.domain.models import PageArtifacts, TextRegion
from app.inpainting.base import Inpainter
from app.ocr.base import OCREngine
from app.rendering.base import TextRenderer


ProgressCallback = Callable[[str, int], None]


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    source_language: str = "auto"
    target_language: str = "zh-Hans"
    review_threshold: float = 0.6
    translate: bool = True


@dataclass(frozen=True, slots=True)
class PipelineResult:
    artifacts: PageArtifacts
    regions: list[TextRegion]


class PagePipeline:
    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def __init__(self, *, detector: TextDetector, ocr: OCREngine, translator, inpainter: Inpainter, renderer: TextRenderer) -> None:
        self.detector = detector
        self.ocr = ocr
        self.translator = translator
        self.inpainter = inpainter
        self.renderer = renderer

    async def process(self, source_path: Path, output_dir: Path, options: PipelineOptions, *, on_progress: ProgressCallback | None = None) -> PipelineResult:
        source_path = Path(source_path)
        if source_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的图片格式: {source_path.suffix}")
        image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法读取图片: {source_path}")
        artifacts = self._prepare_artifacts(source_path, Path(output_dir))
        shutil.copy2(source_path, artifacts.original)
        self._progress(on_progress, "检测", 10)
        regions = await self.detector.detect(image)
        self._progress(on_progress, "OCR", 30)
        regions = await self.ocr.recognize(image, regions)
        for region in regions:
            region.needs_review = region.confidence < options.review_threshold
        if options.translate:
            self._progress(on_progress, "翻译", 55)
            active_regions = [region for region in regions if region.source_text.strip()]
            if active_regions:
                translations = await self.translator.translate([region.source_text for region in active_regions], options.source_language, options.target_language)
                for region, translated_text in zip(active_regions, translations, strict=True):
                    region.translated_text = translated_text
        self._progress(on_progress, "擦字", 75)
        inpainted = await self.inpainter.inpaint(image, regions)
        cv2.imwrite(str(artifacts.inpainted), inpainted)
        self._progress(on_progress, "渲染", 90)
        rendered = await self.renderer.render(inpainted, regions)
        cv2.imwrite(str(artifacts.translated), rendered)
        self._save_metadata(artifacts.metadata, source_path, options, regions)
        self._progress(on_progress, "保存", 100)
        return PipelineResult(artifacts=artifacts, regions=regions)

    @staticmethod
    def _prepare_artifacts(source_path: Path, output_dir: Path) -> PageArtifacts:
        directories = {name: output_dir / name for name in ("original", "inpainted", "translated", "metadata")}
        for directory in directories.values():
            directory.mkdir(parents=True, exist_ok=True)
        stem = source_path.stem
        return PageArtifacts(original=directories["original"] / source_path.name, inpainted=directories["inpainted"] / f"{stem}_inpainted.png", translated=directories["translated"] / f"{stem}_translated.png", metadata=directories["metadata"] / f"{stem}.json")

    @staticmethod
    def _save_metadata(path: Path, source_path: Path, options: PipelineOptions, regions: list[TextRegion]) -> None:
        payload = {"schema_version": 1, "source_path": str(source_path), "source_language": options.source_language, "target_language": options.target_language, "regions": [asdict(region) for region in regions]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _progress(callback: ProgressCallback | None, stage: str, value: int) -> None:
        if callback is not None:
            callback(stage, value)

