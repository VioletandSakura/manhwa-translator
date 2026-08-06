from __future__ import annotations

import cv2
import numpy as np

from app.domain.models import TextRegion


def merge_text_regions(regions: list[TextRegion]) -> list[TextRegion]:
    """Merge nearby same-direction boxes that likely belong to one bubble.

    RapidOCR sometimes splits one speech bubble into several text boxes.
    Merging them keeps the whole sentence in one region so translation and
    rendering cover the full bubble instead of leaving parts untranslated.
    """
    if len(regions) < 2:
        return list(regions)
    merged = sorted(regions, key=lambda region: (region.bounds[1], region.bounds[0]))
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                if _should_merge(merged[i], merged[j]):
                    merged[i] = _merge(merged[i], merged[j])
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _should_merge(first: TextRegion, second: TextRegion) -> bool:
    if first.direction != second.direction:
        return False
    if not (_region_text(first) or _region_text(second)):
        return False
    x0a, y0a, x1a, y1a = first.bounds
    x0b, y0b, x1b, y1b = second.bounds
    width_a, height_a = max(1, x1a - x0a), max(1, y1a - y0a)
    width_b, height_b = max(1, x1b - x0b), max(1, y1b - y0b)
    if max(height_a, height_b) / min(height_a, height_b) > 2.5:
        return False
    if max(width_a, width_b) / min(width_a, width_b) > 2.5:
        return False
    overlap_x = max(0, min(x1a, x1b) - max(x0a, x0b))
    overlap_y = max(0, min(y1a, y1b) - max(y0a, y0b))
    gap_y = max(y0a, y0b) - min(y1a, y1b)
    gap_x = max(x0a, x0b) - min(x1a, x1b)
    if first.direction == "horizontal":
        if overlap_x / min(width_a, width_b) < 0.3:
            return False
        if gap_y > 0.7 * min(height_a, height_b):
            return False
    else:
        if overlap_y / min(height_a, height_b) < 0.3:
            return False
        if gap_x > 0.7 * min(width_a, width_b):
            return False
    combined_text = "\n".join(
        text for text in (_region_text(first), _region_text(second)) if text
    )
    return len(combined_text) <= 120


def _merge(first: TextRegion, second: TextRegion) -> TextRegion:
    points = np.asarray(first.polygon + second.polygon, dtype=np.float32)
    hull = cv2.convexHull(points).reshape(-1, 2).astype(int)
    polygon = [(int(x), int(y)) for x, y in hull.tolist()]
    ordered = sorted(
        [first, second],
        key=lambda region: (region.bounds[1], region.bounds[0]),
    )
    source_text = "\n".join(_region_text(region) for region in ordered if _region_text(region))
    metadata = dict(first.metadata)
    metadata["merged"] = True
    merged_from = list(first.metadata.get("merged_from", [first.id]))
    if second.id not in merged_from:
        merged_from.append(second.id)
    metadata["merged_from"] = merged_from
    if "ocr_source_text" in first.metadata or "ocr_source_text" in second.metadata:
        metadata["ocr_source_text"] = source_text
        metadata["ocr_confidence"] = (
            _region_confidence(first) + _region_confidence(second)
        ) / 2
    return TextRegion(
        id=first.id,
        polygon=polygon,
        source_text=source_text,
        confidence=(
            _region_confidence(first) + _region_confidence(second)
        ) / 2,
        direction=first.direction,
        angle=first.angle,
        needs_review=first.needs_review or second.needs_review,
        metadata=metadata,
    )


def _region_text(region: TextRegion) -> str:
    text = region.source_text.strip()
    if text:
        return text
    return str(region.metadata.get("ocr_source_text") or "").strip()


def _region_confidence(region: TextRegion) -> float:
    if "ocr_confidence" in region.metadata:
        return float(region.metadata["ocr_confidence"] or 0.0)
    return float(region.confidence or 0.0)
