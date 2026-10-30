from __future__ import annotations

import numpy as np

from app.domain.models import TextRegion


class FallbackInpainter:
    """优先使用 LaMa 擦字，失败时自动回退到 OpenCV。"""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_error: str | None = None

    async def inpaint(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        try:
            return await self.primary.inpaint(image, regions)
        except Exception as error:
            self.last_error = str(error)
            return await self.fallback.inpaint(image, regions)

    async def inpaint_mask(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        try:
            return await self.primary.inpaint_mask(image, mask)
        except Exception as error:
            self.last_error = str(error)
            return await self.fallback.inpaint_mask(image, mask)
