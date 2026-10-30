from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from app.domain.enums import PageStatus, TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.services.cached_translator import CachedTranslator
from app.services.original_archive import archive_originals
from app.services.text_exchange import TextExchangeService
from app.storage.database import Database
from app.tasks.queue import PersistentTaskQueue
from app.translation.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleTranslator, TranslationCountError
from app.ui.controller import MainController
from app.ui.main_window import MainWindow
from test_application_services import MemoryCredentialBackend


def _database(tmp_path, count=3):
    database = Database(tmp_path / "app.db")
    database.initialize()
    task_id = database.create_task("chapter", str(tmp_path / "out"))
    pages = [database.add_page(task_id, i, f"{i + 1}.png") for i in range(count)]
    return database, task_id, pages


def test_startup_recovery_preserves_complete_failed_and_cancelled_pages(tmp_path):
    database, task, pages = _database(tmp_path, 5)
    for page_id, status in zip(pages[:4], [PageStatus.COMPLETED, PageStatus.RUNNING, PageStatus.FAILED, PageStatus.CANCELLED]):
        database.update_page_status(page_id, status, stage="旧阶段", progress=100 if status is PageStatus.COMPLETED else 55)
    database.update_task_status(task, TaskStatus.RUNNING)
    database.set_page_result(pages[0], "saved.png")
    database.close()
    reopened = Database(database.path)
    assert reopened.recover_interrupted_tasks() == 1
    assert reopened.get_task(task).status is TaskStatus.PAUSED
    assert [p.status for p in reopened.list_task_pages(task)] == [PageStatus.COMPLETED, PageStatus.PENDING, PageStatus.FAILED, PageStatus.CANCELLED, PageStatus.PENDING]
    assert reopened.get_page(pages[0]).result_path == "saved.png"
    assert reopened.recover_interrupted_tasks() == 0
    counts = reopened.task_page_counts()[task]
    assert counts == {"completed": 1, "pending": 2, "failed": 1, "cancelled": 1}


@pytest.mark.parametrize("action", ["stop", "cancel"])
@pytest.mark.parametrize("stage", ["检测", "翻译", "渲染"])
def test_safe_stop_at_stage_boundaries_skips_later_steps_and_pending_pages(tmp_path, action, stage):
    database, task, pages = _database(tmp_path)
    steps = []

    async def process(page, _output, progress):
        for current in ["检测", "翻译", "渲染"]:
            await progress.checkpoint()
            steps.append((page.id, current))
            progress(current, 30)
            if current == stage:
                getattr(queue, action)(task)

    queue = PersistentTaskQueue(database, process)
    asyncio.run(queue.run(task))
    expected = PageStatus.PENDING if action == "stop" else PageStatus.CANCELLED
    # Finishing the last step may commit a complete page; earlier steps leave it resumable.
    if stage == "渲染":
        assert database.get_page(pages[0]).status is PageStatus.COMPLETED
    else:
        assert database.get_page(pages[0]).status is expected
    assert all(database.get_page(p).status is expected for p in pages[1:])
    assert all(page_id == pages[0] for page_id, _ in steps)
    assert database.get_task(task).status is (TaskStatus.PAUSED if action == "stop" else TaskStatus.CANCELLED)


def test_pause_waits_before_next_step_and_cancel_releases_paused_queue(tmp_path):
    database, task, pages = _database(tmp_path, 2)
    reached = asyncio.Event()
    entered_next = []

    async def scenario():
        async def process(page, _output, progress):
            queue.pause(task)
            reached.set()
            await progress.checkpoint()
            entered_next.append(page.id)

        queue = PersistentTaskQueue(database, process)
        running = asyncio.create_task(queue.run(task))
        await reached.wait()
        await asyncio.sleep(0.08)
        assert entered_next == []
        assert not running.done()
        queue.cancel(task)
        await asyncio.wait_for(running, 1)

    asyncio.run(scenario())
    assert entered_next == []
    assert all(database.get_page(p).status is PageStatus.CANCELLED for p in pages)


def test_cancel_during_retry_delay_never_starts_another_attempt(tmp_path):
    database, task, pages = _database(tmp_path, 1)
    failed_once = asyncio.Event()
    async def process(*_args):
        failed_once.set()
        raise RuntimeError("transient")

    async def scenario():
        queue = PersistentTaskQueue(database, process, max_page_attempts=3, page_retry_delay_seconds=30)
        running = asyncio.create_task(queue.run(task))
        await failed_once.wait()
        queue.cancel(task)
        await asyncio.wait_for(running, 1)
    asyncio.run(scenario())
    assert database.get_page(pages[0]).attempt_count == 1
    assert database.get_page(pages[0]).status is PageStatus.CANCELLED


def test_external_async_cancellation_resets_running_pages_and_closes_resources(tmp_path):
    database, task, pages = _database(tmp_path, 2)
    started = asyncio.Event()
    closed = []
    async def process(*_args):
        started.set()
        await asyncio.Event().wait()
    async def close():
        closed.append(True)
    async def scenario():
        queue = PersistentTaskQueue(database, process, on_run_finished=close)
        running = asyncio.create_task(queue.run(task))
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    asyncio.run(scenario())
    assert closed == [True]
    assert database.get_task(task).status is TaskStatus.PAUSED
    assert all(database.get_page(p).status is PageStatus.PENDING for p in pages)


def test_single_page_retry_does_not_retry_other_failed_pages(tmp_path):
    database, task, pages = _database(tmp_path)
    for page in pages:
        database.update_page_status(page, PageStatus.FAILED, stage="失败", progress=0)
    processed = []
    async def process(page, *_args):
        processed.append(page.id)
    queue = PersistentTaskQueue(database, process)
    queue.retry_page(pages[1])
    asyncio.run(queue.run(task, page_ids=[pages[1]]))
    assert processed == [pages[1]]
    assert [p.status for p in database.list_task_pages(task)] == [PageStatus.FAILED, PageStatus.COMPLETED, PageStatus.FAILED]
    with pytest.raises(ValueError, match="只能重试"):
        queue.retry_page(pages[1])


def test_count_mismatch_repairs_only_affected_chunk_once():
    requests = []
    async def handler(request):
        texts = json.loads(json.loads(request.content)["messages"][1]["content"])["texts"]
        requests.append(texts)
        translations = [] if texts == ["c", "d"] else [f"译:{text}" for text in texts]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": translations})}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(OpenAICompatibleConfig("test", "fake", "https://example.test", "model", max_texts_per_request=2), client=client)
    assert asyncio.run(translator.translate(["a", "b", "c", "d"], "en", "zh")) == ["译:a", "译:b", "译:c", "译:d"]
    assert requests == [["a", "b"], ["c", "d"], ["c"], ["d"]]
    asyncio.run(client.aclose())


def test_failed_chunk_preserves_earlier_cache_and_never_retries_entire_page(tmp_path):
    database, task, pages = _database(tmp_path, 1)
    calls = []
    healthy = False
    class Provider:
        async def translate(self, texts, *_args):
            calls.append(list(texts))
            if not healthy and texts == ["80"]:
                raise TranslationCountError("数量不匹配")
            return ["译:" + text for text in texts]
    cached = CachedTranslator(database, Provider(), provider="test", model="model")
    async def process(page, *_args):
        await cached.translate([str(i) for i in range(81)], "en", "zh")
    queue = PersistentTaskQueue(database, process, max_page_attempts=3)
    asyncio.run(queue.run(task))
    assert database.get_page(pages[0]).attempt_count == 1
    assert [len(x) for x in calls] == [80, 1]
    healthy = True
    queue.retry_page(pages[0])
    asyncio.run(queue.run(task, page_ids=[pages[0]]))
    assert [len(x) for x in calls] == [80, 1, 1]
    assert database.get_task(task).status is TaskStatus.COMPLETED


def _originals(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    for name in ["a.png", "b.png"]:
        (original / name).write_bytes((name * 100).encode())
    return original, {"a.png", "b.png"}


@pytest.mark.parametrize("damage", ["invalid_zip", "wrong_names", "different_content", "bad_crc"])
def test_invalid_existing_archive_never_deletes_copies(tmp_path, damage):
    original, names = _originals(tmp_path)
    target = tmp_path / "originals.zip"
    if damage == "invalid_zip":
        target.write_bytes(b"broken")
    else:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for name in names:
                archive.writestr("other.png" if name == "a.png" and damage == "wrong_names" else name,
                                 b"different" if damage == "different_content" else (original / name).read_bytes())
        if damage == "bad_crc":
            with zipfile.ZipFile(target) as archive:
                info = archive.getinfo("a.png")
                offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
            blob = bytearray(target.read_bytes())
            blob[offset] ^= 0xFF
            target.write_bytes(blob)
    before = {path.name: path.read_bytes() for path in original.iterdir()}
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        archive_originals(tmp_path, names)
    assert {path.name: path.read_bytes() for path in original.iterdir()} == before


def test_archive_interrupted_during_copy_deletion_is_recoverable(tmp_path, monkeypatch):
    original, names = _originals(tmp_path)
    original_unlink = Path.unlink
    def fail_second(path, *args, **kwargs):
        if path.parent == original and path.name == "b.png":
            raise OSError("模拟中断")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail_second)
        with pytest.raises(OSError):
            archive_originals(tmp_path, names)
    assert not (original / "a.png").exists()
    assert (original / "b.png").exists()
    with zipfile.ZipFile(tmp_path / "originals.zip") as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == names
    archive_originals(tmp_path, names)
    assert not original.exists()


def _exchange(tmp_path):
    paths = [tmp_path / f"{i}.json" for i in [1, 2]]
    for i, path in enumerate(paths, 1):
        path.write_text(json.dumps({"source_path": f"chapter/{i}.png", "regions": [{"id": f"r{i}", "source_text": f"source{i}", "translated_text": "old", "polygon": [[0, 0], [10, 0], [10, 10]], "needs_review": True}]}), encoding="utf-8")
    service = TextExchangeService()
    incoming = tmp_path / "exchange.txt.json"
    service.export_json(paths, incoming)
    data = service.import_json(incoming)
    for page in data["pages"]:
        page["regions"][0]["translated_text"] = "new:" + page["page_id"]
        page["regions"][0]["polygon"] = [[999, 999]]
        page["regions"][0]["needs_review"] = False
    data["pages"].reverse()
    incoming.write_text(json.dumps(data), encoding="utf-8")
    return service, paths, incoming, data


def test_json_reordered_pages_update_by_identity_preserving_geometry_and_review(tmp_path):
    service, paths, incoming, _ = _exchange(tmp_path)
    assert service.import_into_metadata(paths, incoming) == 2
    for i, path in enumerate(paths, 1):
        region = json.loads(path.read_text())["regions"][0]
        assert region["translated_text"] == f"new:{i}"
        assert region["polygon"] == [[0, 0], [10, 0], [10, 10]]
        assert region["needs_review"] is True


@pytest.mark.parametrize("bad", ["duplicate_page", "wrong_source", "wrong_region", "wrong_original", "non_text", "duplicate_region"])
def test_json_all_pages_validated_before_any_file_changes(tmp_path, bad):
    service, paths, incoming, data = _exchange(tmp_path)
    page = data["pages"][-1]
    if bad == "duplicate_page":
        data["pages"][-1] = data["pages"][0]
    elif bad == "wrong_source":
        page["source_path"] = "another-book/1.png"
    elif bad == "wrong_region":
        page["regions"][0]["id"] = "missing"
    elif bad == "wrong_original":
        page["regions"][0]["source_text"] = "wrong"
    elif bad == "non_text":
        page["regions"][0]["translated_text"] = []
    else:
        page["regions"].append(page["regions"][0].copy())
    incoming.write_text(json.dumps(data), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError):
        service.import_into_metadata(paths, incoming)
    assert [path.read_bytes() for path in paths] == before


def test_json_disk_failure_rolls_back_every_replaced_page(tmp_path, monkeypatch):
    service, paths, incoming, _ = _exchange(tmp_path)
    before = [path.read_bytes() for path in paths]
    replace = os.replace
    def fail_second(source, target):
        if Path(source).parent.name == "after" and Path(target) == paths[1]:
            raise OSError("模拟写盘失败")
        return replace(source, target)
    with monkeypatch.context() as context:
        context.setattr(os, "replace", fail_second)
        with pytest.raises(OSError):
            service.import_into_metadata(paths, incoming)
    assert [path.read_bytes() for path in paths] == before
    service.recover_pending(tmp_path)
    assert not list(tmp_path.glob(".text-import-*"))


def test_progress_uses_completed_pages_even_when_later_page_finishes_first(tmp_path, qtbot):
    paths = AppPaths(tmp_path / "project", tmp_path / "out", tmp_path / "tmp")
    application = TranslationApplication(paths, credentials=CredentialStore(MemoryCredentialBackend()))
    application.initialize()
    task = application.database.create_task("chapter", str(paths.output_root))
    pages = [application.database.add_page(task, i, f"{i}.png") for i in range(4)]
    window = MainWindow(paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    controller._active_task_id = task
    application.database.update_task_status(task, TaskStatus.RUNNING)
    window.set_queue_pages([Path(f"{i}.png") for i in range(4)])
    application.database.update_page_status(pages[3], PageStatus.COMPLETED, stage="保存", progress=100)
    controller._show_batch_progress(3, 4, 100, "完成")
    assert window.progress_bar.value() == 25
    controller._show_batch_progress(0, 4, 10, "检测")
    assert window.progress_bar.value() == 25
    assert window.queue_table.item(3, 2).text() == "完成"
    controller._refresh_history()
    assert "完成1/4" in window.history_table.item(0, 3).text()
    assert window.history_table.item(0, 2).text() == "处理中"


def test_quit_waits_for_real_qthread_to_stop_and_preserves_task_for_resume(tmp_path, qtbot, monkeypatch):
    import threading
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    paths = AppPaths(tmp_path / "project", tmp_path / "out", tmp_path / "tmp")
    application = TranslationApplication(paths, credentials=CredentialStore(MemoryCredentialBackend()))
    application.initialize()
    task = application.database.create_task("chapter", str(paths.output_root))
    page = application.database.add_page(task, 0, "1.png")
    step_started = threading.Event()
    release_step = threading.Event()
    later_steps = []
    async def process(page, _output, progress):
        step_started.set()
        # Simulates an in-flight OCR/API step, which must finish safely.
        await asyncio.to_thread(release_step.wait)
        await progress.checkpoint()
        later_steps.append(True)
    window = MainWindow(paths)
    qtbot.addWidget(window)
    window.show()
    controller = MainController(application, window)
    controller._active_task_id = task
    controller._active_queue = PersistentTaskQueue(application.database, process)
    controller._queue_running = True
    quits = []
    monkeypatch.setattr(QApplication.instance(), "quit", lambda: quits.append(True))
    async def operation(_progress):
        await controller._active_queue.run(task)
        return task
    controller._start_worker(operation, on_completed=controller._batch_completed, on_failed=lambda error: pytest.fail(error))
    qtbot.waitUntil(step_started.is_set, timeout=2000)
    controller.quit_application()
    QTest.qWait(50)
    assert quits == []
    assert controller._threads
    release_step.set()
    qtbot.waitUntil(lambda: quits == [True], timeout=3000)
    assert not controller._threads
    assert later_steps == []
    assert application.database.get_task(task).status is TaskStatus.PAUSED
    assert application.database.get_page(page).status is PageStatus.PENDING


def test_count_repair_has_bounded_requests_and_never_guesses_alignment():
    requests = []
    async def handler(request):
        texts = json.loads(json.loads(request.content)["messages"][1]["content"])["texts"]
        requests.append(texts)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"translations":[]}'}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(OpenAICompatibleConfig("test", "fake", "https://example.test", "model"), client=client)
    with pytest.raises(TranslationCountError):
        asyncio.run(translator.translate(["a", "b", "c", "d"], "en", "zh"))
    assert requests == [["a", "b", "c", "d"], ["a", "b"]]
    asyncio.run(client.aclose())


def test_router_creates_one_client_per_channel_and_closes_at_end(tmp_path):
    from app.domain.models import ApiChannelRecord
    from app.translation.router import ChannelRouterTranslator
    database, _, _ = _database(tmp_path, 0)
    database.upsert_api_channel(ApiChannelRecord("test", "test", True, "https://example.test", "model", 10, 1, 100, 0, 1, "priority", True, None))
    created, closed = [], []
    class Credentials:
        def load(self, _id):
            return "fake"
    class Translator:
        async def translate(self, texts, *_args):
            return texts
        async def close(self):
            closed.append(True)
    def factory(*_args):
        created.append(True)
        return Translator()
    router = ChannelRouterTranslator(database, Credentials(), factory)
    async def scenario():
        await router.translate(["a"], "en", "zh")
        await router.translate(["b"], "en", "zh")
        await router.close()
    asyncio.run(scenario())
    assert created == closed == [True]


def test_resume_restores_offline_switch_instead_of_starting_paid_translation(tmp_path, qtbot, monkeypatch):
    paths = AppPaths(tmp_path / "project", tmp_path / "out", tmp_path / "tmp")
    application = TranslationApplication(paths, credentials=CredentialStore(MemoryCredentialBackend()))
    application.initialize()
    task = application.database.create_task("chapter", str(paths.output_root))
    application.database.add_page(task, 0, "1.png")
    application.database.set_setting(f"task.{task}.options", json.dumps({
        "translate": False, "review_threshold": 0.85, "source_language": "ja", "target_language": "zh-Hans",
        "render_settings": {"min_font_size": 12, "max_font_size": 48}, "concurrent_pages": 2,
    }))
    window = MainWindow(paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.translate_checkbox.setChecked(True)
    window.history_table.selectRow(0)
    received = []
    monkeypatch.setattr(controller, "_run_persisted_task", lambda task_id, options: received.append((task_id, options)))
    monkeypatch.setattr(application, "validate_translation_configuration", lambda: pytest.fail("Offline resume must not validate/use a paid service"))
    controller.resume_history_task()
    assert received[0][0] == task
    assert received[0][1].translate is False
    assert received[0][1].review_threshold == 0.85
    assert received[0][1].max_font_size == 48
    assert window.concurrent_pages.value() == 2


def test_atomic_artifact_failure_keeps_existing_result(tmp_path, monkeypatch):
    from app.storage.atomic import write_bytes
    target = tmp_path / "page.json"
    target.write_bytes(b"previous complete result")
    def denied(*_args):
        raise PermissionError("模拟写盘失败")
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        write_bytes(target, b"new result")
    assert target.read_bytes() == b"previous complete result"
    assert list(tmp_path.iterdir()) == [target]


def test_completed_history_opens_results_without_api_or_new_worker(tmp_path, qtbot, monkeypatch):
    paths = AppPaths(tmp_path / "project", tmp_path / "out", tmp_path / "tmp")
    application = TranslationApplication(paths, credentials=CredentialStore(MemoryCredentialBackend()))
    application.initialize()
    task = application.database.create_task("chapter", str(paths.output_root))
    page = application.database.add_page(task, 0, "1.png")
    application.database.update_page_status(page, PageStatus.COMPLETED, stage="保存", progress=100)
    application.database.update_task_status(task, TaskStatus.COMPLETED)
    window = MainWindow(paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.history_table.selectRow(0)
    monkeypatch.setattr(application, "validate_translation_configuration", lambda: pytest.fail("No API needed to open completed history"))
    monkeypatch.setattr(controller, "_start_worker", lambda *args, **kwargs: pytest.fail("Completed history must not start work"))
    controller.resume_history_task()
    assert controller._active_page_ids == [page]
    assert window.queue_table.item(0, 2).text() == "完成"
    assert window.progress_bar.value() == 100


def test_single_instance_guard_rejects_second_start_before_repairing_database(tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.main import create_application
    paths = AppPaths(tmp_path / "project", tmp_path / "out", tmp_path / "tmp")
    monkeypatch.setattr(AppPaths, "default", classmethod(lambda cls: paths))
    qt, window, controller = create_application()
    qtbot.addWidget(window)
    task = controller.application.database.create_task("active", str(paths.output_root))
    controller.application.database.update_task_status(task, TaskStatus.RUNNING)
    try:
        with pytest.raises(RuntimeError, match="已经在运行"):
            create_application()
        assert controller.application.database.get_task(task).status is TaskStatus.RUNNING
    finally:
        qt._instance_lock.unlock()
        del qt._instance_lock
        controller.application.database.close()


def test_cancel_between_chunks_keeps_returned_translation_and_sends_no_next_chunk(tmp_path):
    database, task, pages = _database(tmp_path, 1)
    calls = []
    class Provider:
        async def translate(self, texts, *_args):
            calls.append(texts)
            queue.cancel(task)
            return ["译:" + text for text in texts]
    cache = CachedTranslator(database, Provider(), provider="test", model="model")
    async def process(*_args):
        await cache.translate([str(i) for i in range(81)], "en", "zh")
    queue = PersistentTaskQueue(database, process)
    asyncio.run(queue.run(task))
    assert len(calls) == 1 and len(calls[0]) == 80
    assert database.get_translation_cache(provider="test", model="model", source_language="en", target_language="zh", source_text="79") == "译:79"
    assert database.get_page(pages[0]).status is PageStatus.CANCELLED


def test_cancel_after_rate_limit_response_does_not_send_network_retry(tmp_path):
    database, task, pages = _database(tmp_path, 1)
    requests = []
    async def handler(request):
        requests.append(True)
        queue.cancel(task)
        return httpx.Response(429, headers={"Retry-After": "30"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(OpenAICompatibleConfig("test", "fake", "https://example.test", "model", retries=3), client=client)
    async def process(*_args):
        await translator.translate(["a"], "en", "zh")
    queue = PersistentTaskQueue(database, process, max_page_attempts=3)
    asyncio.run(asyncio.wait_for(queue.run(task), 1))
    assert requests == [True]
    assert database.get_page(pages[0]).status is PageStatus.CANCELLED
    asyncio.run(client.aclose())


@pytest.mark.parametrize("no_tray", [True, False])
def test_real_qt_event_loop_exits_after_close_or_tray_quit(tmp_path, no_tray):
    import subprocess
    import sys
    program = '''
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from app.paths import AppPaths
from app.services.application import TranslationApplication
from app.ui.controller import MainController
from app.ui.main_window import MainWindow
from app.security.credentials import CredentialStore
class Empty:
    def get_password(self, *_args): return None
qt = QApplication([])
qt.setQuitOnLastWindowClosed(False)
root = Path(sys.argv[1])
application = TranslationApplication(AppPaths(root/'project', root/'out', root/'tmp'), credentials=CredentialStore(Empty()))
application.initialize()
window = MainWindow(application.paths)
controller = MainController(application, window)
window.show()
if sys.argv[2] == 'True':
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)
    QTimer.singleShot(10, window.close)
else:
    QTimer.singleShot(10, window.request_quit)
QTimer.singleShot(1500, lambda: qt.exit(27))
result = qt.exec()
application.database.close()
raise SystemExit(result)
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), str(no_tray)], env=environment,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
