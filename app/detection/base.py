from typing import Protocol

import numpy as np

from app.domain.models import TextRegion


class TextDetector(Protocol):
    async def detect(self, image: np.ndarray) -> list[TextRegion]: ...

