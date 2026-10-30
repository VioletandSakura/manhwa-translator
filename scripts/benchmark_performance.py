"""Fixed local OCR/pipeline/rerender/GUI benchmarks; no credentials or paid API.

--code-root imports an immutable app snapshot in this fresh process. Peak RSS
is cumulative process peak, not a stage-specific peak. GUI tests replay 40
messages over 200/500-page fixtures, not a full-book translation workload.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time


def memory_usage():
    if os.name != "nt":
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return {"process_peak_rss_bytes": int(peak * (1 if sys.platform == "darwin" else 1024))}
        except ImportError:
            return {}
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ["peak_working", "working", "peak_paged", "paged",
                "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private"]]
    kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {}
    return {"rss_bytes": counters.working, "process_peak_rss_bytes": counters.peak_working,
            "private_bytes": counters.private}


async def measured(operation):
    started, cpu = time.perf_counter(), time.process_time()
    result = await operation()
    return result, {"wall_seconds": time.perf_counter() - started,
                    "cpu_seconds": time.process_time() - cpu, **memory_usage()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root, code_root = args.output_dir.resolve(), args.code_root.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Use a new/empty output directory; existing evidence is preserved")
    if not (code_root / "app/__init__.py").is_file():
        parser.error("--code-root must contain the app package")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0, str(code_root))
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import app as app_package
    from app.detection.rapidocr_detector import RapidOCRDetector
    from app.domain.enums import PageStatus, TaskStatus
    from app.domain.models import PageRecord, TaskRecord
    from app.inpainting.opencv_inpainter import OpenCVInpainter
    from app.ocr.rapidocr_engine import RapidOCREngine
    from app.paths import AppPaths
    from app.rendering.pillow_renderer import PillowTextRenderer
    from app.security.credentials import CredentialStore
    from app.services.application import TranslationApplication
    from app.tasks.pipeline import PagePipeline, PipelineOptions
    from app.ui.controller import MainController
    from app.ui.main_window import MainWindow
    assert Path(app_package.__file__).resolve().is_relative_to(code_root)
    manifest = {str(p.relative_to(code_root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((code_root / "app").rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    (root / "code-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    versions = {}
    for package in ["PySide6", "rapidocr", "onnxruntime", "opencv-python", "numpy", "Pillow"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    report = {"code_root": str(code_root), "app_imported_from": app_package.__file__,
        "code_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "environment": {"python": sys.version, "platform": platform.platform(), "logical_cpus": os.cpu_count(),
                        "packages": versions, "qt_platform": "offscreen"},
        "paid_api_requests": 0, "credential_backend": "empty fixture", "samples": [],
        "notes": ["Peak RSS is cumulative process peak, not isolated per stage.",
                  "Local timings are observations, not a universal speed guarantee.",
                  "GUI scenarios replay 40 identical-shaped messages for each page count."]}
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 46)
    report["environment"]["sample_font_sha256"] = hashlib.sha256(font_path.read_bytes()).hexdigest()
    source_dir = root / "samples"
    source_dir.mkdir()
    sample_paths = []
    for index, (width, height, texts) in enumerate([
        (1200, 1600, ["GOOD MORNING, MY FRIEND!", "PLEASE WAIT FOR ME!"]),
        (1600, 2400, ["LET US GO HOME TOGETHER.", "WE CAN START AGAIN TOMORROW."]),
    ], 1):
        picture = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(picture)
        for line, text in enumerate(texts):
            top = int(height * (.12 + line * .46))
            draw.rectangle((30, top - 70, width - 30, top + 350), outline="#303030", width=4)
            draw.ellipse((80, top, width - 80, top + 230), outline="#303030", width=3)
            draw.text((width // 2, top + 115), text, font=font, anchor="mm", fill="black")
        path = source_dir / f"{index:03}.png"
        picture.save(path)
        sample_paths.append(path)
        report["samples"].append({"file": path.name, "dimensions": [width, height], "source_texts": texts,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    def read_image(path):
        return cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    class EmptyCredentials:
        def get_password(self, *_args): return None
    class NoTranslator:
        async def translate(self, *_args):
            raise AssertionError("OCR-only benchmark must not translate")
    application = TranslationApplication(AppPaths(root / "runtime", root / "outputs", root / "temp"),
                                         credentials=CredentialStore(EmptyCredentials()))
    application.initialize()
    engine = RapidOCREngine()

    async def image_benchmarks():
        first, calls = read_image(sample_paths[0]), []
        for label, image in [("cold_first_infer", first), ("warm_same_page_1", first),
                            ("warm_same_page_2", first), ("warm_second_page", read_image(sample_paths[1]))]:
            output, metric = await measured(lambda image=image: asyncio.to_thread(engine.infer, image))
            payload = output[0] if isinstance(output, tuple) else output
            texts_value, scores_value = getattr(payload, "txts", None), getattr(payload, "scores", None)
            boxes_value = getattr(payload, "boxes", None)
            real_engine = engine._engine
            calls.append({"label": label, **metric, "texts": [] if texts_value is None else list(texts_value),
                          "scores": [] if scores_value is None else [float(s) for s in scores_value],
                          "boxes": [] if boxes_value is None else np.asarray(boxes_value).tolist(),
                          "engine_parameters": {"max_side_len": getattr(real_engine, "max_side_len", None),
                              "det_limit_side_len": getattr(real_engine.text_det, "limit_side_len", None),
                              "det_limit_type": getattr(real_engine.text_det, "limit_type", None)}})
        report["ocr_infer"] = calls
        report["ocr_warm_median_wall_seconds"] = statistics.median(call["wall_seconds"] for call in calls[1:])
        print("OCR cold/warm complete", flush=True)
        pipeline = PagePipeline(detector=RapidOCRDetector(engine), ocr=engine, translator=NoTranslator(),
                                inpainter=OpenCVInpainter(), renderer=PillowTextRenderer())
        results = []
        for path in sample_paths:
            events, started = [], time.perf_counter()
            def progress(stage, value):
                events.append({"stage": stage, "progress": value, "at_seconds": time.perf_counter() - started})
            result, metric = await measured(lambda path=path: pipeline.process(path, application.paths.output_root,
                PipelineOptions(translate=False), on_progress=progress))
            durations = [{"stage": events[i]["stage"], "wall_seconds": events[i+1]["at_seconds"] - events[i]["at_seconds"]}
                         for i in range(len(events) - 1)]
            results.append({"source": path.name, **metric, "stages": durations, "progress_events": events,
                "preparation_wall_seconds": events[0]["at_seconds"] if events else None, "regions": len(result.regions),
                "source_texts": [r.source_text for r in result.regions],
                "artifacts": {key: str(value) for key, value in asdict(result.artifacts).items()}})
        report["ocr_only_pipeline"] = results
        print("OCR-only pipeline complete", flush=True)
        metadata = application.paths.output_root / "metadata/001.json"
        document = json.loads(metadata.read_text(encoding="utf-8"))
        assert document["regions"], "Fixture must contain detectable text"
        for region in document["regions"]:
            region["translated_text"], region["needs_review"] = "早上好，今天也一起出发吧！", False
        metadata.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        redraws = []
        for size in [28, 30, 32]:
            document = json.loads(metadata.read_text(encoding="utf-8"))
            for region in document["regions"]:
                region.setdefault("metadata", {})["font_size"] = size
            metadata.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            target, metric = await measured(lambda: application.rerender_metadata(metadata))
            redraws.append({"font_size": size, **metric, "result_sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        report["style_rerender"] = redraws
        print("Repeated style rerender complete", flush=True)

    asyncio.run(image_benchmarks())
    qt = QApplication.instance() or QApplication([])
    gui_results = []
    for count in [200, 500]:
        task_id = f"benchmark-{count}"
        pages = [PageRecord(f"p-{i}", task_id, i, f"{i+1:04}.png", PageStatus.PENDING, "等待", 0, None) for i in range(count)]
        task = TaskRecord(task_id, "generated fixture", str(root / "gui"), TaskStatus.RUNNING, "", "")
        class FixtureDatabase:
            def __init__(self): self.calls = {"list_task_pages": 0, "get_task": 0, "get_page": 0}
            def list_tasks(self, *_args, **_kwargs): return []
            def task_page_counts(self): return {}
            def get_setting(self, _key, default=""): return default
            def list_task_pages(self, _id):
                self.calls["list_task_pages"] += 1
                return list(pages)
            def get_task(self, _id):
                self.calls["get_task"] += 1
                return task
            def get_page(self, page_id):
                self.calls["get_page"] += 1
                return pages[int(page_id.split("-")[-1])]
        class FixtureApplication:
            def load_api_config(self):
                return {"service_name": "fixture", "base_url": "", "model": "", "timeout_seconds": 60,
                        "retries": 0, "has_api_key": False}
        database = FixtureDatabase()
        fixture_application = FixtureApplication()
        fixture_application.database = database
        window = MainWindow(AppPaths(root / f"gui-{count}", root / "outputs", root / "temp"))
        controller = MainController(fixture_application, window)
        window.show()
        window.resize(1024, 700)
        window.set_queue_pages([Path(page.source_path) for page in pages])
        controller._active_task_id = task_id
        controller._active_page_ids = [page.id for page in pages]
        controller._show_batch_progress(0, count, 0, "准备")
        qt.processEvents()
        row_updates, original_update = [0], window.update_queue_page
        def counted_update(*values):
            row_updates[0] += 1
            original_update(*values)
        window.update_queue_page = counted_update

        def replay(label, snapshot=False):
            for index, page in enumerate(pages):
                pages[index] = replace(page, status=PageStatus.PENDING, stage="等待", progress=0)
            if hasattr(controller, "_load_progress_snapshot"):
                controller._load_progress_snapshot(task_id, pages)
            else:
                window.set_queue_pages([Path(page.source_path) for page in pages])
            controller._queue_running = True
            qt.processEvents()
            database.calls = dict.fromkeys(database.calls, 0)
            row_updates[0] = 0
            started, cpu = time.perf_counter(), time.process_time()
            for index in range(40):
                page_index = index % count
                pages[page_index] = replace(pages[page_index], status=PageStatus.RUNNING, stage="OCR", progress=30)
                if snapshot:
                    controller.page_record_ready.emit(pages[page_index])
                else:
                    controller._show_batch_progress(page_index, count, 30, "OCR")
                if index % 10 == 9: qt.processEvents()
            qt.processEvents()
            dispatch = time.perf_counter() - started
            QTest.qWait(150)
            qt.processEvents()
            if snapshot: controller._flush_page_updates()
            return {"route": label, "messages": 40, "dispatch_wall_seconds": dispatch,
                "wall_seconds_including_150ms_settle": time.perf_counter() - started,
                "cpu_seconds": time.process_time() - cpu, "row_updates": row_updates[0],
                "database_calls": dict(database.calls), "final_progress": window.progress_bar.value(), **memory_usage()}
        routes = [replay("compatibility_callback")]
        if hasattr(controller, "_record_page_update"):
            routes.append(replay("actual_page_snapshot_signal", snapshot=True))
        gui_results.append({"pages": count, "routes": routes})
        window._allow_quit = True
        window.close()
        qt.processEvents()
    report["gui_progress"], report["memory_at_end"] = gui_results, memory_usage()
    application.database.close()
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(root / "report.json"), "ocr_warm_median_seconds": report["ocr_warm_median_wall_seconds"],
                      "gui": gui_results}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
