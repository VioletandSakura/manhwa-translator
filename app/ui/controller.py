from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Coroutine

from PySide6.QtCore import QObject, QThread, Signal, Slot

from app.security.redaction import redact_text
from app.services.application import ProcessingOutcome, TranslationApplication
from app.tasks.pipeline import PipelineOptions

from .main_window import MainWindow


class AsyncWorker(QObject):
    progress = Signal(str, int)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation: Callable[[Callable[[str, int], None]], Coroutine]) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(self.operation(self.progress.emit))
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(redact_text(str(error)))
        finally:
            self.finished.emit()


class MainController(QObject):
    def __init__(self, application: TranslationApplication, window: MainWindow) -> None:
        super().__init__(window)
        self.application = application
        self.window = window
        self._threads: set[QThread] = set()
        self._load_config()
        window.start_requested.connect(self.start_translation)
        window.api_save_requested.connect(self.save_api_config)
        window.api_test_requested.connect(self.test_api)
        window.api_revoke_requested.connect(self.revoke_api_key)

    def _load_config(self) -> None:
        config = self.application.load_api_config()
        self.window.api_service_name.setText(str(config["service_name"]))
        self.window.api_base_url.setText(str(config["base_url"]))
        self.window.api_model.setText(str(config["model"]))
        self.window.api_timeout.setValue(int(config["timeout_seconds"]))
        self.window.api_retries.setValue(int(config["retries"]))
        if config["has_api_key"]:
            self.window.api_key.setPlaceholderText("已安全保存：••••••••")

    @Slot()
    def save_api_config(self) -> None:
        try:
            self.application.save_api_config(self.window.api_values())
        except Exception as error:
            self.window.api_status.setText(f"状态：保存失败 — {redact_text(str(error))}")
            return
        self.window.api_key.clear()
        self.window.api_key.setPlaceholderText("已安全保存：••••••••")
        self.window.api_status.setText("状态：配置已安全保存")
        self.window.append_log("API配置已保存，密钥未写入SQLite或日志。")

    @Slot()
    def revoke_api_key(self) -> None:
        self.application.revoke_api_key()
        self.window.api_key.clear()
        self.window.api_key.setPlaceholderText("密钥仅保存到Windows凭据管理器")
        self.window.api_status.setText("状态：本地密钥已撤销")

    @Slot()
    def test_api(self) -> None:
        self.save_api_config()
        self.window.api_status.setText("状态：正在测试…")

        async def operation(_progress):
            return await self.application.test_api_connection()

        self._start_worker(
            operation,
            on_completed=lambda _: self.window.api_status.setText("状态：连接成功"),
            on_failed=lambda message: self.window.api_status.setText(f"状态：连接失败 — {message}"),
        )

    @Slot()
    def start_translation(self) -> None:
        source_path = Path(self.window.source_edit.text().strip())
        output_dir = Path(self.window.output_edit.text().strip())
        if not source_path.is_file():
            self.window.show_error("请选择有效的漫画图片。")
            return
        if not str(output_dir):
            self.window.show_error("请选择输出目录。")
            return
        self.window.set_original_preview(source_path)
        self.window.set_busy(True)
        self.window.set_progress("准备", 0)
        self.window.append_log(f"开始处理：{source_path.name}")
        options = PipelineOptions(
            source_language=str(self.window.source_language.currentData()),
            target_language=str(self.window.target_language.currentData()),
            review_threshold=self.window.review_threshold.value() / 100,
            translate=self.window.translate_checkbox.isChecked(),
        )

        async def operation(progress):
            return await self.application.process_page(
                source_path,
                output_dir,
                options,
                on_progress=progress,
            )

        self._start_worker(operation, on_completed=self._translation_completed, on_failed=self._translation_failed)

    def _translation_completed(self, outcome: ProcessingOutcome) -> None:
        translated_path = outcome.result.artifacts.translated
        self.window.set_result_preview(translated_path)
        self.window.set_progress("完成", 100)
        self.window.append_log(f"处理完成：{translated_path}")
        self.window.set_busy(False)

    def _translation_failed(self, message: str) -> None:
        self.window.set_progress("失败", 0)
        self.window.append_log(f"处理失败：{message}")
        self.window.set_busy(False)
        self.window.show_error(message)

    def _start_worker(self, operation, *, on_completed, on_failed) -> None:
        thread = QThread(self)
        worker = AsyncWorker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.window.set_progress)
        worker.completed.connect(on_completed)
        worker.failed.connect(on_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._threads.discard(thread))
        self._threads.add(thread)
        thread.start()

