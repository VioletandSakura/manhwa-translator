from typing import Protocol

import numpy as np

from app.domain.models import TextRegion


class TextRenderer(Protocol):
    async def render(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray: ...

