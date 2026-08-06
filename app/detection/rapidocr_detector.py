from __future__ import annotations

import asyncio
import math
import uuid

import numpy as np

from app.domain.models import TextRegion
from app.ocr.rapidocr_engine import RapidOCREngine

from .region_merger import merge_text_regions


class RapidOCRDetector:
    """使用PP-OCR检测框，并缓存同次推理得到的识别文本。"""

    def __init__(self, ocr: RapidOCREngine) -> None:
        self.ocr = ocr

    async def detect(self, image: np.ndarray) -> list[TextRegion]:
        output = await asyncio.to_thread(self.ocr.infer, image)
        payload = output[0] if isinstance(output, tuple) else output
        boxes = self._as_list(getattr(payload, "boxes", None))
        texts = self._as_list(getattr(payload, "txts", None))
        scores = self._as_list(getattr(payload, "scores", None))
        regions: list[TextRegion] = []
        for index, box in enumerate(boxes):
            points = [(int(round(point[0])), int(round(point[1]))) for point in np.asarray(box)]
            if len(points) != 4:
                continue
            xs, ys = [point[0] for point in points], [point[1] for point in points]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            delta_x = points[1][0] - points[0][0]
            delta_y = points[1][1] - points[0][1]
            angle = math.degrees(math.atan2(delta_y, delta_x))
            text = str(texts[index]) if index < len(texts) else ""
            confidence = float(scores[index]) if index < len(scores) else 0.0
            regions.append(
                TextRegion(
                    id=uuid.uuid4().hex,
                    polygon=points,
                    direction="vertical" if height > width * 1.3 else "horizontal",
                    angle=angle,
                    metadata={
                        "detector": "rapidocr",
                        "ocr_source_text": text,
                        "ocr_confidence": confidence,
                    },
                )
            )
        return merge_text_regions(regions)

    @staticmethod
    def _as_list(value) -> list:
        return [] if value is None else list(value)
