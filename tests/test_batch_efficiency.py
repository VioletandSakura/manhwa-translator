from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import threading

from PIL import Image
from PySide6.QtCore import QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
import pytest

from app.domain.enums import PageStatus, TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database
from app.tasks.queue import PersistentTaskQueue
from app.ui.controller import MainController
from app.ui.main_window import MainWindow


def _application(tmp_path):
    class EmptyCredentials:
        def get_password(self, *_args):
            return None
    paths = AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp")
    application = TranslationApplication(paths, credentials=CredentialStore(EmptyCredentials()))
    application.initialize()
    return application


def test_batch_creation_commits_once_and_rolls_back_incomplete_task(tmp_path):
    database = Database(tmp_path / "app.db")
    database.initialize()
    trace = []
    database.connect().set_trace_callback(trace.append)
    task = database.create_task_with_pages("chapter", "out", [(i, f"{i}.png") for i in range(500)])
    assert len(database.list_task_pages(task)) == 500
    assert sum(statement == "COMMIT" for statement in trace) == 1
    with pytest.raises(sqlite3.IntegrityError):
        database.create_task_with_pages("bad", "out", [(0, "a.png"), (0, "b.png")])
    assert [record.id for record in database.list_tasks()] == [task]
    database.close()


@pytest.mark.parametrize("action", ["cancel", "quit"])
def test_import_and_queue_build_run_off_gui_and_stop_during_preparation(tmp_path, qtbot, monkeypatch, action):
    application = _application(tmp_path)
    source = tmp_path / "sample.png"
    Image.new("RGB", (80, 80), "white").save(source)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    window.show()
    controller = MainController(application, window)
    window.source_edit.setText(str(source))
    window.translate_checkbox.setChecked(False)
    entered, release = threading.Event(), threading.Event()
    threads, processed, errors = [], [], []
    original_import = application.create_batch_task

    def slow_import(*args):
        threads.append(QThread.currentThread() is QApplication.instance().thread())
        entered.set()
        assert release.wait(3)
        return original_import(*args)

    def build_queue(*args, **kwargs):
        threads.append(QThread.currentThread() is QApplication.instance().thread())
        async def process(page, *_args):
            processed.append(page.id)
        return PersistentTaskQueue(application.database, process,
            **{key: kwargs[key] for key in ("on_page_completed", "on_page_failed", "on_page_progress")})

    monkeypatch.setattr(application, "create_batch_task", slow_import)
    monkeypatch.setattr(application, "build_task_queue", build_queue)
    monkeypatch.setattr(window, "show_error", errors.append)
    quits = []
    if action == "quit":
        monkeypatch.setattr(QApplication.instance(), "quit", lambda: quits.append(True))
    pulses = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: pulses.append(True))
    heartbeat.start()
    try:
        controller.start_translation()
        qtbot.waitUntil(entered.is_set, timeout=1000)
        QTest.qWait(70)
        assert len(pulses) >= 3
        assert controller._queue_running and controller._active_queue is None
        if action == "cancel":
            controller.cancel_batch()
        else:
            controller.quit_application()
            assert quits == []
    finally:
        release.set()
    qtbot.waitUntil(lambda: not controller._threads, timeout=4000)
    heartbeat.stop()
    assert threads == [False, False]
    assert processed == [] and errors == []
    task = application.database.list_tasks()[0]
    assert task.status is (TaskStatus.CANCELLED if action == "cancel" else TaskStatus.PAUSED)
    assert application.database.list_task_pages(task.id)[0].status is (PageStatus.CANCELLED if action == "cancel" else PageStatus.PENDING)
    if action == "quit":
        assert quits == [True]
    application.database.close()


def test_progress_snapshots_update_only_changed_rows_without_database_reads(tmp_path, qtbot, monkeypatch):
    application = _application(tmp_path)
    task = application.database.create_task_with_pages("chapter", "out", [(i, f"{i}.png") for i in range(500)])
    pages = application.database.list_task_pages(task)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._load_progress_snapshot(task, pages)
    controller._queue_running = True
    controller._task_state = "running"
    painted = []
    original_paint = window.update_queue_page
    monkeypatch.setattr(window, "update_queue_page", lambda *args: (painted.append(args[0]), original_paint(*args)))
    monkeypatch.setattr(application.database, "list_task_pages", lambda *_args: pytest.fail("progress must not read the full queue"))
    monkeypatch.setattr(application.database, "get_page", lambda *_args: pytest.fail("snapshot events already contain the page"))
    for index in range(40):
        controller._record_page_update(replace(pages[4], status=PageStatus.RUNNING, stage="OCR", progress=index))
    controller._record_page_update(replace(pages[4], status=PageStatus.COMPLETED, stage="保存", progress=100))
    controller._flush_page_updates()
    assert painted == [4]
    assert controller._progress_counts["completed"] == 1
    assert "完成1/500页" in window.stage_label.text()
    # Late signals must not regress the stored terminal state.
    controller._record_page_update(replace(pages[4], status=PageStatus.RUNNING, stage="OCR", progress=50))
    controller._flush_page_updates()
    assert window.queue_table.item(4, 2).text() == "完成"
    application.database.close()


def test_zip_export_keeps_gui_responsive_and_runs_in_worker(tmp_path, qtbot, monkeypatch):
    application = _application(tmp_path)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._active_task_id = application.database.create_task("chapter", "out")
    entered, release = threading.Event(), threading.Event()
    threads, errors = [], []
    target = tmp_path / "result.zip"
    def slow_export(_task, path):
        threads.append(QThread.currentThread() is QApplication.instance().thread())
        entered.set()
        assert release.wait(3)
        path.write_bytes(b"test-export")
        return path
    monkeypatch.setattr(application, "export_task_zip", slow_export)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))
    monkeypatch.setattr(window, "show_error", errors.append)
    try:
        controller.export_zip()
        qtbot.waitUntil(entered.is_set, timeout=1000)
        assert not window.start_button.isEnabled()
        QTest.qWait(50)
        assert controller._threads
    finally:
        release.set()
    qtbot.waitUntil(lambda: not controller._threads, timeout=4000)
    assert threads == [False] and not errors and target.is_file()
    assert window.start_button.isEnabled()
    application.database.close()


def _task_metadata(application, tmp_path):
    import json
    output = tmp_path / "output"
    (output / "metadata").mkdir(parents=True)
    (output / "inpainted").mkdir()
    sources, metadata = [], []
    for index in range(2):
        source = tmp_path / f"{index + 1}.png"
        Image.new("RGB", (160, 120), "white").save(source)
        sources.append(source)
        Image.new("RGB", (160, 120), "white").save(output / "inpainted" / f"{index + 1}_inpainted.png")
        path = output / "metadata" / f"{index + 1}.json"
        path.write_text(json.dumps({"source_path": str(source), "regions": [{
            "id": f"r{index}", "source_text": f"source{index}", "translated_text": "old",
            "polygon": [[10, 10], [100, 10], [100, 60], [10, 60]], "needs_review": bool(index),
        }]}), encoding="utf-8")
        metadata.append(path)
    task = application.database.create_task_with_pages("chapter", str(output), [(i, str(p)) for i, p in enumerate(sources)])
    return task, output, metadata


def test_import_renders_only_changed_page_and_skips_identical_import(tmp_path, qtbot, monkeypatch):
    import json
    from app.services.text_exchange import TextExchangeService
    application = _application(tmp_path)
    task, output, metadata = _task_metadata(application, tmp_path)
    incoming = tmp_path / "incoming.json"
    service = TextExchangeService()
    service.export_json(metadata, incoming)
    document = service.import_json(incoming)
    document["pages"][1]["regions"][0]["translated_text"] = "changed"
    incoming.write_text(json.dumps(document), encoding="utf-8")
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._active_task_id = task
    calls, errors = [], []
    async def render(path, **_options):
        assert QThread.currentThread() is not QApplication.instance().thread()
        calls.append(path)
        target = output / "translated.png"
        Image.new("RGB", (80, 80), "white").save(target)
        return target
    monkeypatch.setattr(application, "rerender_metadata", render)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(incoming), ""))
    monkeypatch.setattr(window, "show_error", errors.append)
    controller.import_text_and_rerender()
    qtbot.waitUntil(lambda: not controller._threads, timeout=3000)
    assert calls == [metadata[1]] and errors == []
    controller.import_text_and_rerender()
    qtbot.waitUntil(lambda: not controller._threads, timeout=3000)
    assert calls == [metadata[1]] and "无需重绘" in window.stage_label.text()
    application.database.close()


def test_review_navigation_saves_current_edits_then_selects_next_page_issue(tmp_path, qtbot, monkeypatch):
    import json
    import numpy as np
    application = _application(tmp_path)
    task, output, metadata = _task_metadata(application, tmp_path)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._active_task_id = task
    errors = []
    monkeypatch.setattr(window, "show_error", errors.append)
    editor = window.page_editor
    editor.load_page(output / "inpainted" / "1_inpainted.png", metadata[0],
                     original_image=np.full((120, 160, 3), 255, dtype=np.uint8))
    editor.select_region("r0")
    editor.translation_text.setText("saved before navigation")
    controller.open_next_review_page()
    assert not editor.isEnabled(), "Search must protect edits made during the asynchronous page switch"
    qtbot.waitUntil(lambda: not controller._threads, timeout=3000)
    assert editor.isEnabled()
    assert errors == []
    assert editor.metadata_path == metadata[1]
    assert editor.selected_region_id() == "r1"
    assert json.loads(metadata[0].read_text(encoding="utf-8"))["regions"][0]["translated_text"] == "saved before navigation"
    application.database.close()


def test_editor_keeps_new_inpaint_cache_when_saving_next_style_edit(tmp_path, qtbot, monkeypatch):
    import asyncio
    import numpy as np
    from app.inpainting.opencv_inpainter import OpenCVInpainter
    from app.tasks.pipeline import PipelineOptions
    application = _application(tmp_path)
    _task, output, metadata = _task_metadata(application, tmp_path)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    editor = window.page_editor
    editor.load_page(output / "inpainted/1_inpainted.png", metadata[0],
                     original_image=np.full((120, 160, 3), 255, dtype=np.uint8))
    editor.select_region("r0")
    calls = []
    class CountingInpainter(OpenCVInpainter):
        async def inpaint_mask(self, image, mask):
            calls.append(True)
            return await super().inpaint_mask(image, mask)
    painter = CountingInpainter()
    monkeypatch.setattr(application, "_build_inpainter", lambda _engine: painter)
    editor.translation_text.setText("first edit")
    editor.save_page()
    options = PipelineOptions(translate=False, min_font_size=10, max_font_size=24)
    target = asyncio.run(application.rerender_metadata(metadata[0], render_options=options))
    editor.show_rendered_preview(target)
    editor.font_size.setValue(18)
    editor.translation_text.setText("second edit")
    editor.save_page()
    asyncio.run(application.rerender_metadata(metadata[0], render_options=options))
    assert calls == [True], "Style edits through the actual editor must preserve the validated base cache"
    application.database.close()


def test_prepared_task_uses_current_intent_instead_of_old_database_status(tmp_path, qtbot):
    application = _application(tmp_path)
    task = application.database.create_task_with_pages("chapter", "out", [(0, "1.png")])
    pages = application.database.list_task_pages(task)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._queue_running = True
    controller._active_task_id = task
    controller._initialize_task_view(task, pages, None)
    assert controller._task_state == "running"
    controller._pending_queue_action = "pause"
    controller._initialize_task_view(task, pages, None)
    assert controller._task_state == "paused"
    controller._pending_queue_action = "cancel"
    controller._initialize_task_view(task, pages, None)
    assert controller._task_state == "cancelled"
    application.database.close()


def test_out_of_order_running_snapshots_do_not_roll_stage_back(tmp_path, qtbot):
    application = _application(tmp_path)
    task = application.database.create_task_with_pages("chapter", "out", [(0, "1.png")])
    pages = application.database.list_task_pages(task)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._load_progress_snapshot(task, pages)
    controller._queue_running = True
    controller._task_state = "running"
    controller._record_page_update(replace(pages[0], status=PageStatus.RUNNING, stage="翻译", progress=55, attempt_count=1))
    controller._record_page_update(replace(pages[0], status=PageStatus.RUNNING, stage="OCR", progress=30, attempt_count=1))
    controller._flush_page_updates()
    assert window.queue_table.item(0, 2).text() == "翻译"
    # A genuine new retry is allowed to start again from preparation.
    controller._record_page_update(replace(pages[0], status=PageStatus.RUNNING, stage="准备", progress=0, attempt_count=2))
    controller._flush_page_updates()
    assert window.queue_table.item(0, 2).text() == "准备"
    application.database.close()
