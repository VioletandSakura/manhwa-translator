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

