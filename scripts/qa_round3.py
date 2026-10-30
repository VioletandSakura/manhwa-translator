"""Isolated crash recovery QA with real OCR and Qt; uses no paid service.

Kills only its own child test process at explicit checkpoints. All inputs,
databases, logs and outputs live inside a new --output-dir.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.services.cached_translator import CachedTranslator
from app.services.text_exchange import TextExchangeService
from app.storage.atomic import write_bytes
from app.tasks.pipeline import PipelineOptions
from app.tasks.queue import PersistentTaskQueue


def write_json(path, document):
    write_bytes(path, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))


def application(root):
    class EmptyCredentials:
        def get_password(self, *_args):
            return None
    app = TranslationApplication(AppPaths(root / "runtime", root / "results", root / "temp"),
                                 credentials=CredentialStore(EmptyCredentials()))
    app.initialize()
    return app


def states(app, task):
    return {"task": str(app.database.get_task(task).status),
            "pages": [{"id": p.id, "status": str(p.status), "attempts": p.attempt_count, "stage": p.stage}
                      for p in app.database.list_task_pages(task)]}


def child_queue(root, stop_stage):
    app = application(root)
    task = app.database.list_tasks()[0].id
    before = states(app, task)
    pipeline = app.build_pipeline(translate=False)

    class OfflineProvider:
        async def translate(self, texts, *_languages):
            path = root / "translation_calls.json"
            calls = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            calls.append({"texts": texts, "mode": "offline fixture"})
            write_json(path, calls)
            return ["测试译文：" + text for text in texts]

    pipeline.translator = CachedTranslator(app.database, OfflineProvider(), provider="round3-offline", model="fixture")
    async def process(page, output, progress):
        def observed(stage, value):
            progress(stage, value)
            if page.page_index == 1 and stage == stop_stage:
                write_json(root / "interrupt-ready.json", states(app, task))
                while True:
                    time.sleep(0.1)
        observed.checkpoint = progress.checkpoint
        return await pipeline.process(Path(page.source_path), output, PipelineOptions(review_threshold=0.5), on_progress=observed)
    queue = PersistentTaskQueue(app.database, process, on_task_completed=app.archive_originals,
                                on_run_finished=pipeline.translator.close)
    asyncio.run(queue.run(task))
    write_json(root / "resume-result.json", {"before": before, "after": states(app, task)})
    app.database.close()


def child_import(root):
    paths = [root / f"{i}.json" for i in (1, 2)]
    real_replace = os.replace
    def interrupted_replace(source, target):
        result = real_replace(source, target)
        if Path(source).parent.name == "after":
            write_json(root / "interrupt-ready.json", {"replaced": Path(target).name})
            while True:
                time.sleep(0.1)
        return result
    os.replace = interrupted_replace
    TextExchangeService().import_into_metadata(paths, root / "incoming.json")


def launch(root, child, stage=""):
    log = (root / f"child-{child}-{'interrupt' if stage else 'resume'}.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--output-dir", str(root),
                                "--child", child, "--stop-stage", stage], stdout=log, stderr=subprocess.STDOUT)
    return process, log


def capture_history(root):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from app.ui.controller import MainController
    from app.ui.main_window import MainWindow
    qt = QApplication.instance() or QApplication(sys.argv[:1])
    app = application(root / "translation_finished")
    window = MainWindow(app.paths)
    controller = MainController(app, window)
    window.show()
    for index in range(window.tabs.count()):
        if window.tabs.tabText(index) == "任务历史":
            window.tabs.setCurrentIndex(index)
    QTest.qWait(200)
    window.grab().save(str(root / "history.png"))
    window.hide()
    app.database.close()


def wait_interrupt(process, root):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (root / "interrupt-ready.json").exists():
            process.kill()
            process.wait(timeout=15)
            return
        if process.poll() is not None:
            raise RuntimeError(f"QA child exited early: {root}")
        time.sleep(0.05)
    process.kill()
    process.wait()
    raise TimeoutError(f"No interruption marker: {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--child", choices=["queue", "import", "history"])
    parser.add_argument("--stop-stage", default="")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if args.child:
        if args.child == "history":
            capture_history(root)
        elif args.child == "queue":
            child_queue(root, args.stop_stage)
        else:
            child_import(root)
        return 0
    if root.exists() and any(root.iterdir()):
        parser.error("Use a new or empty output directory")
    root.mkdir(parents=True, exist_ok=True)
    report = {"mode": "offline, real OCR and Qt", "paid_api_requests": 0, "crashes": {}}
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
    for label, stop_stage in [("detection", "检测"), ("translation_finished", "擦字"), ("files_saved", "保存")]:
        case = root / label
        case.mkdir()
        source = case / "samples"
        source.mkdir()
        app = application(case)
        task = app.database.create_task(str(source), str(case / "results"))
        for i, text in enumerate(["HELLO FRIEND!", "PLEASE WAIT HERE!", "LET US GO HOME!"]):
            image = Image.new("RGB", (900, 600), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((60, 100, 840, 470), outline="#333333", width=4)
            draw.text((450, 285), text, font=font, anchor="mm", fill="black")
            path = source / f"{i+1:03}.png"
            image.save(path)
            app.database.add_page(task, i, str(path))
        app.database.close()
        process, log = launch(case, "queue", stop_stage)
        try:
            wait_interrupt(process, case)
        finally:
            log.close()
        interrupted = json.loads((case / "interrupt-ready.json").read_text(encoding="utf-8"))
        assert [p["status"] for p in interrupted["pages"]] == ["completed", "running", "pending"]
        first = case / "results" / "translated" / "001_translated.png"
        first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
        process, log = launch(case, "queue")
        try:
            assert process.wait(timeout=90) == 0, f"Resume failed: {case}"
        finally:
            log.close()
        result = json.loads((case / "resume-result.json").read_text(encoding="utf-8"))
        assert [p["status"] for p in result["before"]["pages"]] == ["completed", "pending", "pending"]
        assert result["after"]["task"] == "completed"
        assert [p["attempts"] for p in result["after"]["pages"]] == [1, 2, 1]
        assert hashlib.sha256(first.read_bytes()).hexdigest() == first_hash
        calls = json.loads((case / "translation_calls.json").read_text(encoding="utf-8"))
        assert len(calls) == 3, "Completed translations were called again instead of using cache"
        import zipfile
        with zipfile.ZipFile(case / "results" / "originals.zip") as archive:
            assert archive.testzip() is None
            assert len(archive.infolist()) == 3
        report["crashes"][label] = {"interrupted": interrupted, **result,
            "completed_page_unchanged": True, "translation_calls_total": len(calls), "archive_crc_ok": True}
        print(f"PASS crash recovery: {label}", flush=True)

    case = root / "import-crash"
    case.mkdir()
    paths = [case / f"{i}.json" for i in (1, 2)]
    for i, path in enumerate(paths, 1):
        write_json(path, {"source_path": f"{i}.png", "regions": [{"id": "r", "source_text": str(i), "translated_text": "old"}]})
    service = TextExchangeService()
    service.export_json(paths, case / "incoming.json")
    incoming = service.import_json(case / "incoming.json")
    for page in incoming["pages"]:
        page["regions"][0]["translated_text"] = "new"
    write_json(case / "incoming.json", incoming)
    before = [p.read_bytes() for p in paths]
    process, log = launch(case, "import", "crash")
    try:
        wait_interrupt(process, case)
    finally:
        log.close()
    assert paths[0].read_bytes() != before[0] and paths[1].read_bytes() == before[1]
    assert service.recover_pending(case) == 1
    assert [p.read_bytes() for p in paths] == before
    report["import_crash_rolled_back_all_pages"] = True
    print("PASS import crash rollback", flush=True)

    capture_history(root)
    write_json(root / "report.json", report)
    print(f"Report: {root / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
