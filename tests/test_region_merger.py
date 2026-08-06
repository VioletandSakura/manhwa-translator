import pytest

from app.detection.region_merger import merge_text_regions
from app.domain.models import TextRegion


def _region(
    region_id: str,
    polygon: list[tuple[int, int]],
    *,
    text: str = "",
    confidence: float = 0.9,
    direction: str = "horizontal",
    metadata: dict | None = None,
) -> TextRegion:
    return TextRegion(
        id=region_id,
        polygon=polygon,
        source_text=text,
        confidence=confidence,
        direction=direction,
        metadata=metadata or {},
    )


def test_merges_stacked_horizontal_boxes() -> None:
    first = _region("a", [(10, 10), (100, 10), (100, 40), (10, 40)], text="\u4e00\u884c\u76ee")
    second = _region("b", [(10, 50), (100, 50), (100, 80), (10, 80)], text="\u4e8c\u884c\u76ee", confidence=0.8)

    merged = merge_text_regions([first, second])

    assert len(merged) == 1
    assert merged[0].id == "a"
    assert merged[0].source_text == "\u4e00\u884c\u76ee\n\u4e8c\u884c\u76ee"
    assert merged[0].confidence == pytest.approx(0.85)
    assert merged[0].metadata["merged"] is True
    assert merged[0].metadata["merged_from"] == ["a", "b"]


def test_does_not_merge_far_apart_boxes() -> None:
    first = _region("a", [(10, 10), (100, 10), (100, 40), (10, 40)], text="\u4e00")
    second = _region("b", [(10, 200), (100, 200), (100, 230), (10, 230)], text="\u4e8c")

    merged = merge_text_regions([first, second])

    assert len(merged) == 2


def test_does_not_merge_side_by_side_horizontal_boxes() -> None:
    first = _region("a", [(10, 10), (100, 10), (100, 40), (10, 40)], text="\u4e00")
    second = _region("b", [(150, 10), (240, 10), (240, 40), (150, 40)], text="\u4e8c")

    merged = merge_text_regions([first, second])

    assert len(merged) == 2


def test_merges_vertical_boxes_side_by_side() -> None:
    first = _region(
        "a",
        [(100, 10), (120, 10), (120, 100), (100, 100)],
        text="\u7b2c\u4e00\u5217",
        direction="vertical",
    )
    second = _region(
        "b",
        [(125, 10), (145, 10), (145, 100), (125, 100)],
        text="\u7b2c\u4e8c\u5217",
        direction="vertical",
    )

    merged = merge_text_regions([first, second])

    assert len(merged) == 1
    assert merged[0].source_text == "\u7b2c\u4e00\u5217\n\u7b2c\u4e8c\u5217"


def test_does_not_merge_mixed_directions() -> None:
    first = _region("a", [(10, 10), (100, 10), (100, 40), (10, 40)], text="\u4e00")
    second = _region(
        "b",
        [(10, 50), (30, 50), (30, 140), (10, 140)],
        text="\u4e8c",
        direction="vertical",
    )

    merged = merge_text_regions([first, second])

    assert len(merged) == 2


def test_merges_chain_of_three_boxes() -> None:
    first = _region("a", [(10, 10), (100, 10), (100, 40), (10, 40)], text="\u4e00")
    second = _region("b", [(10, 50), (100, 50), (100, 80), (10, 80)], text="\u4e8c")
    third = _region("c", [(10, 90), (100, 90), (100, 120), (10, 120)], text="\u4e09")

    merged = merge_text_regions([first, second, third])

    assert len(merged) == 1
    assert merged[0].source_text == "\u4e00\n\u4e8c\n\u4e09"
    assert merged[0].metadata["merged_from"] == ["a", "b", "c"]


def test_merger_reads_text_from_cached_detection_metadata() -> None:
    first = _region(
        "a",
        [(10, 10), (100, 10), (100, 40), (10, 40)],
        metadata={"ocr_source_text": "\u4e00\u884c\u76ee", "ocr_confidence": 0.9},
    )
    second = _region(
        "b",
        [(10, 50), (100, 50), (100, 80), (10, 80)],
        metadata={"ocr_source_text": "\u4e8c\u884c\u76ee", "ocr_confidence": 0.75},
    )

    merged = merge_text_regions([first, second])

    assert len(merged) == 1
    assert merged[0].source_text == "\u4e00\u884c\u76ee\n\u4e8c\u884c\u76ee"
    assert merged[0].confidence == pytest.approx(0.825)
    assert merged[0].metadata["ocr_source_text"] == "\u4e00\u884c\u76ee\n\u4e8c\u884c\u76ee"
    assert merged[0].metadata["ocr_confidence"] == 0.825
