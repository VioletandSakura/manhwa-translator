import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.paths import AppPaths
from app.services.application import ProcessingOutcome
from app.ui.controller import AsyncWorker, MainController
from app.ui.main_window import MainWindow


class FakeApplication:
    def __init__(self) -> None:
        self.saved = None
        self.revoked = False

    def load_api_config(self):
        return {
            "service_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "timeout_seconds": 60,
            "retries": 2,
            "has_api_key": True,
        }

    def save_api_config(self, values):
        self.saved = values

    def revoke_api_key(self):
        self.revoked = True

    async def test_api_connection(self):
        return "连接成功"

    async def list_api_channel_models(self, _channel_id):
        return ["deepseek-flash", "deepseek-v4-pro"]

    async def process_page(self, source_path, output_dir, options, *, on_progress=None):
        on_progress("保存", 100)
        translated = Path(output_dir) / "translated.png"
        return ProcessingOutcome("task", "page", SimpleNamespace(artifacts=SimpleNamespace(translated=translated)))


def test_controller_loads_saves_and_revokes_api_config(qtbot) -> None:
    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)

    assert "已安全保存" in window.api_key.placeholderText()
    controller.save_api_config()
    assert application.saved["model"] == "deepseek-chat"
    controller.revoke_api_key()
    assert application.revoked is True


def test_async_worker_emits_progress_and_result(qtbot) -> None:
    async def operation(progress):
        progress("OCR", 30)
        return "完成"

    worker = AsyncWorker(operation)
    progress_values = []
    results = []
    worker.progress.connect(lambda stage, value: progress_values.append((stage, value)))
    worker.completed.connect(results.append)

    worker.run()

    assert progress_values == [("OCR", 30)]
    assert results == ["完成"]


def test_worker_completion_closure_runs_on_gui_thread(qtbot) -> None:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(FakeApplication(), window)
    observed = []

    async def operation(progress):
        return "result"

    controller._start_worker(
        operation,
        on_completed=lambda result: observed.append((result, QThread.currentThread() == QApplication.instance().thread())),
        on_failed=lambda message: observed.append(message),
    )
    qtbot.waitUntil(lambda: not controller._threads, timeout=5000)

    assert observed == [("result", True)]


def test_controller_runs_valid_page_and_handles_completion(tmp_path, qtbot, monkeypatch) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"image")
    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(tmp_path))
    captured = {}

    def capture_worker(operation, *, on_completed, on_failed):
        captured["operation"] = operation
        captured["completed"] = on_completed

    monkeypatch.setattr(controller, "_start_worker", capture_worker)
    controller.start_translation()
    outcome = asyncio.run(captured["operation"](window.set_progress))
    captured["completed"](outcome)

    assert window.progress_bar.value() == 100
    assert window.start_button.isEnabled()


def test_controller_shows_redacted_failure(qtbot, monkeypatch) -> None:
    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)
    shown = []
    monkeypatch.setattr(window, "show_error", shown.append)

    controller._translation_failed("请求失败")

    assert shown == ["请求失败"]
    assert window.stage_label.text() == "当前阶段：失败"


def test_batch_setup_failure_restores_idle_controls(tmp_path, qtbot, monkeypatch) -> None:
    application = FakeApplication()
    application.create_batch_task = lambda *args: SimpleNamespace(task_id="task")
    application.build_task_queue = lambda *args: None
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)
    source = tmp_path / "page.png"
    source.write_bytes(b"image")
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(tmp_path / "out"))
    shown = []
    monkeypatch.setattr(window, "show_error", shown.append)

    def unavailable(*args):
        raise ValueError("尚未配置可用的API通道")

    monkeypatch.setattr(application, "validate_translation_configuration", unavailable, raising=False)
    controller.start_translation()

    assert shown == ["尚未配置可用的API通道"]
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()


def test_output_failure_is_reported_before_task_is_created(tmp_path, qtbot, monkeypatch) -> None:
    import app.ui.controller as controller_module

    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)
    source = tmp_path / "page.png"
    source.write_bytes(b"image")
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(tmp_path / "out"))
    shown = []
    monkeypatch.setattr(window, "show_error", shown.append)

    def denied(output_dir):
        raise ValueError("无法写入输出目录")

    monkeypatch.setattr(controller_module, "ensure_output_writable", denied)
    controller.start_translation()

    assert shown == ["无法写入输出目录"]
    assert window.start_button.isEnabled()
    assert not controller._threads


def test_api_connection_worker_restores_button_and_status(qtbot) -> None:
    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)

    controller.test_api()

    qtbot.waitUntil(lambda: "连接成功" in window.api_status.text(), timeout=3000)
    assert window.api_test_button.isEnabled()
    assert window.api_test_button.text() == "测试连通性"


def test_controller_fetches_models_into_editable_selector(qtbot) -> None:
    application = FakeApplication()
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    controller = MainController(application, window)

    controller.fetch_api_models()

    qtbot.waitUntil(lambda: window.api_model.count() == 2, timeout=3000)
    assert window.api_model.isEditable()
    assert window.api_model.itemText(0) == "deepseek-flash"
    assert window.api_model.itemText(1) == "deepseek-v4-pro"
    assert window.api_models_button.isEnabled()
    assert "获取到2个模型" in window.api_status.text()
