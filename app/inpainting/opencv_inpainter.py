from __future__ import annotations

import cv2
import numpy as np

from app.domain.models import TextRegion


class OpenCVInpainter:
    """OpenCV 局部擦字实现。"""

    def __init__(self, *, dilation: int = 5, radius: int = 5) -> None:
        self.dilation = max(0, dilation)
        self.radius = max(1, radius)

    async def inpaint(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        if not regions:
            return image.copy()
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for region in regions:
            cv2.fillPoly(mask, [np.asarray(region.polygon, dtype=np.int32)], 255)
        if self.dilation:
            size = self.dilation * 2 + 1
            mask = cv2.dilate(mask, np.ones((size, size), np.uint8), iterations=1)
        return cv2.inpaint(image, mask, self.radius, cv2.INPAINT_TELEA)
