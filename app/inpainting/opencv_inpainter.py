from __future__ import annotations

import cv2
import numpy as np

from app.domain.models import TextRegion
from .masks import region_mask


class OpenCVInpainter:
    """OpenCV 局部擦字实现。"""

    def __init__(self, *, dilation: int = 5, radius: int = 5) -> None:
        self.dilation = max(0, dilation)
        self.radius = max(1, radius)

    async def inpaint(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        return await self.inpaint_mask(image, region_mask(image.shape, regions, self.dilation))

    async def inpaint_mask(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.shape != image.shape[:2]:
            raise ValueError("蒙版尺寸与原图不一致。")
        if not np.any(mask):
            return image.copy()
        return cv2.inpaint(image, mask, self.radius, cv2.INPAINT_TELEA)
