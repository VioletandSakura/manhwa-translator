import asyncio
import builtins
from types import SimpleNamespace

import numpy as np
import pytest

from app.domain.models import TextRegion
from app.ocr.rapidocr_engine import RapidOCREngine, RapidOCRUnavailableError
from app.detection.rapidocr_detector import RapidOCRDetector


def _region() -> TextRegion:
    return TextRegion(id="one", polygon=[(0, 0), (80, 0), (80, 40), (0, 40)])


def test_rapidocr_reads_current_result_object() -> None:
    engine = lambda crop: SimpleNamespace(txts=["こんにちは"], scores=[0.9])
    ocr = RapidOCREngine(engine)

    regions = asyncio.run(ocr.recognize(np.zeros((50, 100, 3), dtype=np.uint8), [_region()]))

    assert regions[0].source_text == "こんにちは"
    assert regions[0].confidence == pytest.approx(0.9)


def test_rapidocr_reads_legacy_list_result() -> None:
    engine = lambda crop: [([0, 0, 1, 1], "hello", 0.8)]
    ocr = RapidOCREngine(engine)

    regions = asyncio.run(ocr.recognize(np.zeros((50, 100, 3), dtype=np.uint8), [_region()]))

    assert regions[0].source_text == "hello"
    assert regions[0].confidence == pytest.approx(0.8)


def test_rapidocr_marks_empty_crop_for_review() -> None:
    region = TextRegion(id="outside", polygon=[(200, 200), (210, 200), (210, 210), (200, 210)])
    ocr = RapidOCREngine(lambda crop: None)

    regions = asyncio.run(ocr.recognize(np.zeros((20, 20, 3), dtype=np.uint8), [region]))

    assert regions[0].needs_review is True


def test_missing_rapidocr_has_chinese_install_message(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rapidocr":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ocr = RapidOCREngine()

    with pytest.raises(RapidOCRUnavailableError, match="尚未安装本地OCR组件"):
        ocr._get_engine()


def test_rapidocr_detector_reuses_recognition_result() -> None:
    output = SimpleNamespace(
        boxes=np.asarray([[[10, 10], [90, 10], [90, 40], [10, 40]]]),
        txts=np.asarray(["漫画文字"]),
        scores=np.asarray([0.88]),
    )
    ocr = RapidOCREngine(lambda image: output)
    detector = RapidOCRDetector(ocr)

    regions = asyncio.run(detector.detect(np.zeros((100, 120, 3), dtype=np.uint8)))
    recognized = asyncio.run(ocr.recognize(np.zeros((100, 120, 3), dtype=np.uint8), regions))

    assert recognized[0].source_text == "漫画文字"
    assert recognized[0].confidence == pytest.approx(0.88)
    assert recognized[0].metadata["detector"] == "rapidocr"


def test_rapidocr_recognition_runs_in_worker_thread() -> None:
    import threading

    observed: list[str] = []

    def engine(crop):
        observed.append(threading.current_thread().name)
        return SimpleNamespace(txts=["ok"], scores=[0.9])

    ocr = RapidOCREngine(engine)

    asyncio.run(ocr.recognize(np.zeros((50, 100, 3), dtype=np.uint8), [_region()]))

    assert observed
    assert observed[0] != threading.main_thread().name


class _RefineEngine:
    def __init__(self, text: str, confidence: float) -> None:
        self.text = text
        self.confidence = confidence
        self.calls = 0

    def recognize_txt(self, crops):
        self.calls += 1
        return SimpleNamespace(txts=[self.text], scores=[self.confidence])


def _cached_region(text: str, confidence: float) -> TextRegion:
    return TextRegion(
        id="cached",
        polygon=[(10, 10), (100, 10), (100, 40), (10, 40)],
        metadata={"ocr_source_text": text, "ocr_confidence": confidence},
    )


def test_rapidocr_refines_low_confidence_cached_region() -> None:
    engine = _RefineEngine("\u5165\u3063\u3066", 0.82)
    ocr = RapidOCREngine(engine)
    region = _cached_region("\u5165\u3066", 0.535)

    regions = asyncio.run(ocr.recognize(np.zeros((60, 120, 3), dtype=np.uint8), [region]))

    assert regions[0].source_text == "\u5165\u3063\u3066"
    assert regions[0].confidence == pytest.approx(0.82)
    assert regions[0].metadata.get("ocr_refined") is True
    assert engine.calls >= 1


def test_rapidocr_keeps_high_confidence_cached_region_untouched() -> None:
    engine = _RefineEngine("\u5165\u3063\u3066", 0.99)
    ocr = RapidOCREngine(engine)
    region = _cached_region("\u5165\u3066", 0.9)

    regions = asyncio.run(ocr.recognize(np.zeros((60, 120, 3), dtype=np.uint8), [region]))

    assert regions[0].source_text == "\u5165\u3066"
    assert regions[0].confidence == pytest.approx(0.9)
    assert "ocr_refined" not in regions[0].metadata
    assert engine.calls == 0


def test_rapidocr_rejects_unrelated_refinement_reading() -> None:
    engine = _RefineEngine("213", 0.7)
    ocr = RapidOCREngine(engine)
    region = _cached_region("LMEIS", 0.548)

    regions = asyncio.run(ocr.recognize(np.zeros((60, 120, 3), dtype=np.uint8), [region]))

    assert regions[0].source_text == "LMEIS"
    assert "ocr_refined" not in regions[0].metadata
