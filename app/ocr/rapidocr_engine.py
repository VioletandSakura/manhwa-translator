from __future__ import annotations

from typing import Any

import numpy as np

from app.domain.models import TextRegion


class RapidOCRUnavailableError(RuntimeError):
    pass


class RapidOCREngine:
    """延迟加载RapidOCR，避免程序启动时下载或载入模型。"""

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr import RapidOCR
        except ImportError as error:
            raise RapidOCRUnavailableError("尚未安装本地OCR组件。请在OCR设置中确认安装后再试。") from error
        self._engine = RapidOCR()
        return self._engine

    async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]:
        engine = self._get_engine()
        height, width = image.shape[:2]
        for region in regions:
            if "ocr_source_text" in region.metadata:
                region.source_text = str(region.metadata.get("ocr_source_text") or "")
                region.confidence = float(region.metadata.get("ocr_confidence") or 0.0)
                continue
            left, top, right, bottom = region.bounds
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            crop = image[top:bottom, left:right]
            if crop.size == 0:
                region.needs_review = True
                continue
            text, confidence = self._recognize_crop(engine, crop)
            region.source_text = text
            region.confidence = confidence
        return regions

    @staticmethod
    def _recognize_crop(engine: Any, crop: np.ndarray) -> tuple[str, float]:
        result = engine(crop)
        payload = result[0] if isinstance(result, tuple) else result
        if payload is None:
            return "", 0.0
        if hasattr(payload, "txts"):
            texts = list(payload.txts or [])
            scores = [float(value) for value in (payload.scores or [])]
        elif isinstance(payload, list):
            texts = [str(item[1]) for item in payload if len(item) >= 3]
            scores = [float(item[2]) for item in payload if len(item) >= 3]
        else:
            return "", 0.0
        confidence = sum(scores) / len(scores) if scores else 0.0
        return "\n".join(texts).strip(), confidence
