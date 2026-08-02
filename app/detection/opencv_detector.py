from __future__ import annotations

import uuid

import cv2
import numpy as np

from app.domain.models import TextRegion


class OpenCVTextDetector:
    """轻量离线保底检测器；专用漫画模型可通过同一接口替换。"""

    def __init__(self, *, min_area: int = 80, max_area_ratio: float = 0.35) -> None:
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio

    async def detect(self, image: np.ndarray) -> list[TextRegion]:
        if image.size == 0:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        normalized = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
        merged = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_kernel, iterations=2)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        image_area = image.shape[0] * image.shape[1]
        regions: list[TextRegion] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            if area < self.min_area or area > image_area * self.max_area_ratio:
                continue
            if width < 4 or height < 4:
                continue
            direction = "vertical" if height > width * 1.3 else "horizontal"
            regions.append(
                TextRegion(
                    id=uuid.uuid4().hex,
                    polygon=[(x, y), (x + width, y), (x + width, y + height), (x, y + height)],
                    direction=direction,
                    metadata={"detector": "opencv", "area": area},
                )
            )
        return sorted(regions, key=lambda region: (region.bounds[1], region.bounds[0]))

