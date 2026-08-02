from typing import Protocol

import numpy as np

from app.domain.models import TextRegion


class Inpainter(Protocol):
    async def inpaint(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray: ...

