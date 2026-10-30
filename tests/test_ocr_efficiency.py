from __future__ import annotations

import asyncio
from itertools import permutations
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from app.detection.region_merger import merge_text_regions
from app.domain.models import TextRegion
from app.ocr.rapidocr_engine import RapidOCREngine


def cached_region():
    return TextRegion("cached", [(10, 10), (100, 10), (100, 40), (10, 40)],
                      metadata={"ocr_source_text": "入て", "ocr_confidence": .53})


class RefiningEngine:
    def __init__(self, readings):
        self.readings, self.calls = readings, 0

    def recognize_txt(self, crops):
        text, confidence = self.readings[self.calls % len(self.readings)]
        self.calls += 1
        return SimpleNamespace(txts=[text], scores=[confidence])


def test_real_initial_defaults_restore_even_when_first_load_is_high_accuracy(monkeypatch):
    original = SimpleNamespace(max_side_len=2312, text_det=SimpleNamespace(limit_side_len=832))
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=lambda: original))
    ocr = RapidOCREngine(det_limit_side_len=1280, max_side_len=4000)
    ocr._get_engine()
    assert (original.max_side_len, original.text_det.limit_side_len) == (4000, 1280)
    ocr.set_detection_config()
    assert (original.max_side_len, original.text_det.limit_side_len) == (2312, 832)
    ocr.set_detection_config(det_limit_side_len=1600, max_side_len=5000)
    ocr.set_detection_config()
    assert (original.max_side_len, original.text_det.limit_side_len) == (2312, 832)


def test_detection_config_waits_for_running_inference():
    started, release, configuring, configured = (threading.Event() for _ in range(4))
    observations = []
    class Engine:
        max_side_len = 2000
        text_det = SimpleNamespace(limit_side_len=736)
        def __call__(self, _image):
            observations.append((self.max_side_len, self.text_det.limit_side_len))
            started.set()
            assert release.wait(2)
            observations.append((self.max_side_len, self.text_det.limit_side_len))
    engine = Engine()
    ocr = RapidOCREngine(engine)
    worker = threading.Thread(target=ocr.infer, args=(np.zeros((30, 60, 3), np.uint8),))
    def reconfigure():
        configuring.set()
        ocr.set_detection_config(det_limit_side_len=1280, max_side_len=4000)
        configured.set()
    updater = threading.Thread(target=reconfigure)
    worker.start()
    assert started.wait(1)
    updater.start()
    assert configuring.wait(1)
    try:
        assert not configured.wait(.05)
    finally:
        release.set()
        worker.join(2)
        updater.join(2)
    assert not worker.is_alive() and not updater.is_alive()
    assert observations == [(2000, 736), (2000, 736)]
    assert (engine.max_side_len, engine.text_det.limit_side_len) == (4000, 1280)


def test_none_can_itself_be_the_original_library_default():
    engine = SimpleNamespace(max_side_len=None, text_det=SimpleNamespace(limit_side_len=None))
    ocr = RapidOCREngine(engine, det_limit_side_len=1280, max_side_len=4000)
    ocr._get_engine()
    ocr.set_detection_config()
    assert engine.max_side_len is None and engine.text_det.limit_side_len is None


def test_stable_high_confidence_refinement_stops_after_two_candidates(monkeypatch):
    engine = RefiningEngine([("入って", .99)])
    ocr = RapidOCREngine(engine)
    monkeypatch.setattr(ocr, "_clahe", lambda _image: pytest.fail("Unused CLAHE variant should not be built"))
    region = cached_region()
    asyncio.run(ocr.recognize(np.zeros((60, 120, 3), np.uint8), [region]))
    assert region.source_text == "入って"
    assert engine.calls == 2
    assert region.metadata["ocr_refine_attempts"] == 2
    assert region.metadata["ocr_refine_early_stopped"] is True


def test_unrelated_high_confidence_never_triggers_early_stop():
    engine = RefiningEngine([("UNRELATED", .999)])
    ocr = RapidOCREngine(engine)
    region = cached_region()
    asyncio.run(ocr.recognize(np.zeros((60, 120, 3), np.uint8), [region]))
    assert engine.calls == 6
    assert region.source_text == "入て"
    assert region.metadata["ocr_refine_early_stopped"] is False
    assert "ocr_refined" not in region.metadata


def test_disagreeing_candidates_use_full_budget_instead_of_early_stopping():
    engine = RefiningEngine([("入って", .99), ("入ってよ", .995)])
    ocr = RapidOCREngine(engine)
    region = cached_region()
    asyncio.run(ocr.recognize(np.zeros((60, 120, 3), np.uint8), [region]))
    assert engine.calls == 6
    assert region.metadata["ocr_refine_early_stopped"] is False


def test_candidate_budget_is_controllable_without_changing_acceptance_rules():
    engine = RefiningEngine([("入って", .82)])
    ocr = RapidOCREngine(engine, refine_candidate_budget=2, refine_early_stop_confidence=None)
    region = cached_region()
    asyncio.run(ocr.recognize(np.zeros((60, 120, 3), np.uint8), [region]))
    assert engine.calls == 2 and region.source_text == "入って"
    engine2 = RefiningEngine([("UNRELATED", .99)])
    ocr2 = RapidOCREngine(engine2, refine_candidate_budget=1)
    unchanged = cached_region()
    asyncio.run(ocr2.recognize(np.zeros((60, 120, 3), np.uint8), [unchanged]))
    assert engine2.calls == 1 and unchanged.source_text == "入て"


def test_zero_budget_skips_refinement_and_high_confidence_text_is_never_reprocessed():
    engine = RefiningEngine([("UNRELATED", .999)])
    ocr = RapidOCREngine(engine, refine_candidate_budget=0)
    low = cached_region()
    high = TextRegion("accepted", [(10, 10), (100, 10), (100, 40), (10, 40)],
                      metadata={"ocr_source_text": "已接受文本", "ocr_confidence": .95})
    asyncio.run(ocr.recognize(np.zeros((60, 120, 3), np.uint8), [low, high]))
    assert engine.calls == 0
    assert low.source_text == "入て" and high.source_text == "已接受文本"


@pytest.mark.parametrize("options", [{"refine_candidate_budget": -1}, {"refine_candidate_budget": 7},
                                      {"refine_early_stop_confidence": 1.1}, {"refine_stable_results": 1}])
def test_invalid_refinement_controls_fail_early(options):
    with pytest.raises(ValueError):
        RapidOCREngine(**options)


@pytest.mark.parametrize("ordering", list(permutations([0, 1, 2])))
def test_vertical_column_reading_order_is_right_to_left_for_every_input_order(ordering):
    regions = [TextRegion(str(index), [(x, y), (x+20, y), (x+20, y+90), (x, y+90)],
                          direction="vertical", metadata={"ocr_source_text": text, "ocr_confidence": .9})
               for index, (x, y, text) in enumerate([(100, 10, "左列"), (125, 8, "中列"), (150, 18, "右列")])]
    merged = merge_text_regions([regions[index] for index in ordering])
    assert len(merged) == 1
    assert merged[0].source_text == "右列\n中列\n左列"
    assert merged[0].metadata["ocr_source_text"] == "右列\n中列\n左列"
