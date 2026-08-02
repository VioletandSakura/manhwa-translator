from typing import Protocol

import numpy as np

from app.domain.models import TextRegion


class OCREngine(Protocol):
    async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]: ...

