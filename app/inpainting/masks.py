from __future__ import annotations

import cv2
import numpy as np

from app.domain.models import TextRegion


def region_mask(shape: tuple[int, ...], regions: list[TextRegion], dilation: int = 0) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    for region in regions:
        polygon = region.metadata.get("source_polygon", region.polygon)
        if len(polygon) >= 3:
            cv2.fillPoly(mask, [np.asarray(polygon, dtype=np.int32)], 255)
    if dilation and np.any(mask):
        size = 2 * dilation + 1
        mask = cv2.dilate(mask, np.ones((size, size), np.uint8))
    return mask


def restore_protected(result: np.ndarray, source: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
    mask = region_mask(source.shape, regions, dilation=1)
    result[mask > 0] = source[mask > 0]
    return result
