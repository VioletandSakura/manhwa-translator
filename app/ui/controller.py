from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable, Coroutine

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from app.security.redaction import redact_text
from app.services.application import ProcessingOutcome, TranslationApplication
from app.services.output_paths import batch_requires_name, resolve_batch_output
from app.services.text_exchange import TextExchangeService
from app.tasks.pipeline import PipelineOptions

from .main_window import MainWindow


class AsyncWorker(QObject):
    """在 QThread 中执行异步操作并把结果/错误送回主线程。"""

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
    """界面事件与应用服务的桥接层，管理任务队列和后台线程。"""

    page_preview_ready = Signal(str)
    page_status_ready = Signal(int, str, str)
    page_progress_ready = Signal(int, int, int, str)

    def __init__(self, application: TranslationApplication, window: MainWindow) -> None:
        super().__init__(window)
        self.application = application
        self.window = window
        self._threads: set[QThread] = set()
        self._workers: dict[QThread, AsyncWorker] = {}
        self._active_queue = None
        self._active_task_id: str | None = None
        self._queue_running = False
        self._active_page_ids: list[str] = []
        self._load_config()
        window.start_requested.connect(self.start_translation)
        window.api_save_requested.connect(self.save_api_config)
        window.api_test_requested.connect(self.test_api)
        window.api_models_requested.connect(self.fetch_api_models)
        window.api_revoke_requested.connect(self.revoke_api_key)
        window.api_delete_requested.connect(self.delete_api_channel)
        window.library_save_requested.connect(self.save_library_content)
        window.replacement_save_requested.connect(self.save_replacement_rules)
        window.preset_save_requested.connect(self.save_preset)
        window.preset_apply_requested.connect(self.apply_preset)
        window.history_resume_requested.connect(self.resume_history_task)
        window.retry_requested.connect(self.retry_failed_page)
        window.export_txt_requested.connect(lambda: self.export_text("txt"))
        window.export_json_requested.connect(lambda: self.export_text("json"))
        window.import_text_requested.connect(self.import_text_and_rerender)
        window.export_zip_requested.connect(self.export_zip)
        window.pause_requested.connect(self.pause_batch)
        window.resume_requested.connect(self.resume_batch)
        window.cancel_requested.connect(self.cancel_batch)
        window.exit_requested.connect(self.quit_application)
        self.page_preview_ready.connect(lambda path: self.window.set_result_preview(Path(path)))
        self.page_status_ready.connect(self.window.update_queue_page)
        self.page_progress_ready.connect(self._show_batch_progress)

    def _load_config(self) -> None:
        config = self.application.load_api_config()
        self.window.api_service_name.setText(str(config["service_name"]))
        self.window.api_base_url.setText(str(config["base_url"]))
        self.window.api_model.setCurrentText(str(config["model"]))
        self.window.api_timeout.setValue(int(config["timeout_seconds"]))
        self.window.api_retries.setValue(int(config["retries"]))
        if config["has_api_key"]:
            self.window.api_key.setPlaceholderText("已安全保存：••••••••")
        if hasattr(self.application, "list_api_channels"):
            self.window.set_api_channels(self.application.list_api_channels())
        if hasattr(self.application, "list_library_content"):
            glossary = self.application.list_library_content("glossary")
            prompts = self.application.list_library_content("prompt")
            replacements = self.application.list_library_content("replacement")
            presets = self.application.list_library_content("preset")
            if glossary:
                self.window.glossary_editor.setPlainText(glossary[0].payload)
            if prompts:
                self.window.prompt_editor.setPlainText(prompts[0].payload)
            if replacements:
                self.window.replacement_editor.setPlainText(replacements[0].payload)
            self.window.preset_list.clear()
            for preset in presets:
                self.window.preset_list.addItem(preset.name, preset.id)
        if hasattr(self.application, "database"):
            self.window.set_history(self.application.database.list_tasks())

    @Slot()
    def save_api_config(self) -> bool:
        try:
            if hasattr(self.application, "save_api_channel"):
                channel_id = self.application.save_api_channel(self.window.api_values())
                saved_channel = self.application.database.get_api_channel(channel_id)
                self.window.api_model.setCurrentText(saved_channel.model)
                self.window.set_api_channels(self.application.list_api_channels())
            else:
                self.application.save_api_config(self.window.api_values())
        except Exception as error:
            self.window.api_status.setText(f"状态：保存失败 — {redact_text(str(error))}")
            return False
        self.window.api_key.clear()
        self.window.api_key.setPlaceholderText("已安全保存：••••••••")
        self.window.api_status.setText("状态：配置已安全保存")
        self.window.append_log("API配置已保存，密钥未写入SQLite或日志。")
        return True

    @Slot()
    def revoke_api_key(self) -> None:
        if hasattr(self.application, "revoke_api_channel_key"):
            self.application.revoke_api_channel_key(self.window.api_channel_id.text().strip())
        else:
            self.application.revoke_api_key()
        self.window.api_key.clear()
        self.window.api_key.setPlaceholderText("密钥仅保存到Windows凭据管理器")
        self.window.api_status.setText("状态：本地密钥已撤销")

    @Slot()
    def delete_api_channel(self) -> None:
        if not hasattr(self.application, "delete_api_channel"):
            return
        channel_id = self.window.api_channel_id.text().strip()
        try:
            self.application.delete_api_channel(channel_id)
        except Exception as error:
            self.window.api_status.setText(f"状态：删除失败 · {redact_text(str(error))}")
            return
        self.window.set_api_channels(self.application.list_api_channels())
        self.window.api_status.setText("状态：通道已删除")

    @Slot()
    def save_library_content(self) -> None:
        if not hasattr(self.application, "save_library_content"):
            return
        try:
            self.application.save_library_content("glossary", "当前术语表", self.window.glossary_editor.toPlainText())
            self.application.save_library_content("prompt", "当前提示词", self.window.prompt_editor.toPlainText())
            self.window.append_log("术语表与提示词已保存")
        except Exception as error:
            self.window.show_error(redact_text(str(error)))

    @Slot()
    def save_replacement_rules(self) -> None:
        if hasattr(self.application, "save_library_content"):
            self.application.save_library_content(
                "replacement", "当前替换规则", self.window.replacement_editor.toPlainText()
            )
            self.window.append_log("替换规则已保存")

    @Slot()
    def save_preset(self) -> None:
        if not hasattr(self.application, "save_library_content"):
            return
        name = self.window.preset_name.text().strip()
        if not name:
            self.window.show_error("请输入预设名称")
            return
        payload = json.dumps(
            {
                "api": {key: value for key, value in self.window.api_values().items() if key != "api_key"},
                "ocr_engine": self.window.ocr_engine.currentData(),
                "inpainting_engine": self.window.inpainting_engine.currentData(),
                "high_accuracy_ocr": self.window.high_accuracy_ocr.isChecked(),
                "concurrent_pages": self.window.concurrent_pages.value(),
                "source_language": self.window.source_language.currentData(),
                "target_language": self.window.target_language.currentData(),
            },
            ensure_ascii=False,
        )
        self.application.save_library_content("preset", name, payload)
        self._load_config()
        self.window.append_log(f"预设已保存：{name}")

    @Slot()
    def apply_preset(self) -> None:
        preset_id = self.window.preset_list.currentData()
        if not preset_id:
            self.window.show_error("请先选择一个预设")
            return
        try:
            entry = self.application.database.get_library_entry(str(preset_id))
            payload = json.loads(entry.payload)
            if not isinstance(payload, dict):
                raise ValueError("预设内容格式无效")
            self.window.apply_preset_values(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.window.show_error(redact_text(str(error)))
            return
        self.window.append_log(f"已应用预设：{entry.name}（API Key不会包含在预设中）")

    @Slot()
    def test_api(self) -> None:
        if not self.save_api_config():
            return
        self.window.set_api_test_busy(True)
        self.window.api_status.setText("状态：正在测试（最长15秒，不产生翻译Token）…")

        async def operation(_progress):
            if hasattr(self.application, "test_api_channel"):
                return await self.application.test_api_channel(self.window.api_channel_id.text().strip())
            return await self.application.test_api_connection()

        self._start_worker(
            operation,
            on_completed=self._api_test_completed,
            on_failed=self._api_test_failed,
        )

    def _api_test_completed(self, model: str) -> None:
        self.window.set_api_test_busy(False)
        self.window.api_status.setText(f"状态：连接成功，模型可用 — {model}")
        if hasattr(self.application, "list_api_channels"):
            self.window.set_api_channels(self.application.list_api_channels())

    def _api_test_failed(self, message: str) -> None:
        self.window.set_api_test_busy(False)
        self.window.api_status.setText(f"状态：连接失败 — {message}")
        if hasattr(self.application, "list_api_channels"):
            self.window.set_api_channels(self.application.list_api_channels())

    @Slot()
    def fetch_api_models(self) -> None:
        if not self.save_api_config():
            return
        self.window.set_api_models_busy(True)
        self.window.api_status.setText("状态：正在获取可用模型（最长15秒）…")

        async def operation(_progress):
            return await self.application.list_api_channel_models(
                self.window.api_channel_id.text().strip()
            )

        self._start_worker(
            operation,
            on_completed=self._api_models_completed,
            on_failed=self._api_models_failed,
        )

    def _api_models_completed(self, models: list[str]) -> None:
        self.window.set_api_models_busy(False)
        self.window.set_api_model_choices(models)
        self.window.api_status.setText(f"状态：获取到{len(models)}个模型，请选择后保存并测试")

    def _api_models_failed(self, message: str) -> None:
        self.window.set_api_models_busy(False)
        self.window.api_status.setText(f"状态：获取模型失败 — {message}")

    @Slot()
    def start_translation(self) -> None:
        sources = self.window.selected_sources()
        output_text = self.window.output_edit.text().strip()
        if not sources or any(not source.exists() for source in sources):
            self.window.show_error("请选择有效的漫画图片、文件夹、ZIP或EPUB。")
            return
        if not output_text:
            self.window.show_error("请选择输出目录。")
            return
        try:
            output_dir = resolve_batch_output(
                Path(output_text),
                self.window.batch_name_edit.text(),
                require_name=batch_requires_name(sources),
            )
        except ValueError as error:
            self.window.show_error(str(error))
            return
        source_path = sources[0]
        if source_path.is_file() and source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            self.window.set_original_preview(source_path)
        self.window.set_busy(True)
        self.window.set_progress("准备", 0)
        self.window.append_log(f"开始处理：{source_path.name}")
        options = self._current_options()

        if hasattr(self.application, "create_batch_task") and hasattr(self.application, "build_task_queue"):
            self._start_batch(sources, output_dir, options)
            return

        async def operation(progress):
            return await self.application.process_page(
                source_path,
                output_dir,
                options,
                on_progress=progress,
            )

        self._start_worker(operation, on_completed=self._translation_completed, on_failed=self._translation_failed)

    def _start_batch(self, sources: list[Path], output_dir: Path, options: PipelineOptions) -> None:
        try:
            batch = self.application.create_batch_task(sources, output_dir)
        except Exception as error:
            self._translation_failed(redact_text(str(error)))
            return
        self._run_persisted_task(batch.task_id, options)

    def _current_options(self) -> PipelineOptions:
        return PipelineOptions(
            source_language=str(self.window.source_language.currentData()),
            target_language=str(self.window.target_language.currentData()),
            review_threshold=self.window.review_threshold.value() / 100,
            translate=self.window.translate_checkbox.isChecked(),
        )

    def _run_persisted_task(self, task_id: str, options: PipelineOptions) -> None:
        self._active_task_id = task_id
        pages = self.application.database.list_task_pages(task_id)
        self._active_page_ids = [page.id for page in pages]
        self.window.set_queue_pages([Path(page.source_path) for page in pages])
        total = len(pages)

        def completed(_task_id, page):
            self.page_status_ready.emit(page.page_index, "完成", "")
            if page.result_path:
                self.page_preview_ready.emit(page.result_path)

        def failed(_task_id, page):
            self.page_status_ready.emit(page.page_index, "失败", page.error or "")

        def progressed(_task_id, page, stage, progress):
            self.page_progress_ready.emit(page.page_index, total, progress, stage)

        self._active_queue = self.application.build_task_queue(
            options,
            ocr_engine=str(self.window.ocr_engine.currentData()),
            inpainting_engine=str(self.window.inpainting_engine.currentData()),
            high_accuracy_ocr=self.window.high_accuracy_ocr.isChecked(),
            max_concurrent_pages=self.window.concurrent_pages.value(),
            on_page_completed=completed,
            on_page_failed=failed,
            on_page_progress=progressed,
        )

        async def operation(_progress):
            await self._active_queue.run(task_id)
            return task_id

        self._start_worker(operation, on_completed=self._batch_completed, on_failed=self._translation_failed)
        self._queue_running = True

    @Slot()
    def resume_history_task(self) -> None:
        task_id = self.window.selected_history_task_id()
        if not task_id:
            self.window.show_error("请先在任务历史中选择一个任务")
            return
        try:
            task = self.application.database.get_task(task_id)
            pages = self.application.database.list_task_pages(task_id)
        except KeyError as error:
            self.window.show_error(redact_text(str(error)))
            return
        if not pages:
            self.window.show_error("该任务没有可恢复的页面")
            return
        self.window.output_edit.setText(task.output_dir)
        self.window.set_busy(True)
        self.window.set_progress("恢复任务", 0)
        self.window.append_log(f"恢复历史任务：{task.id[:10]}")
        self._run_persisted_task(task_id, self._current_options())

    def _show_batch_progress(self, page_index: int, total: int, page_progress: int, stage: str) -> None:
        overall = round(((page_index + page_progress / 100) / max(total, 1)) * 100)
        self.window.set_progress(f"第{page_index + 1}/{total}页 · {stage}", overall)
        self.window.update_queue_page(page_index, stage)

    def _batch_completed(self, task_id: str) -> None:
        self._queue_running = False
        task = self.application.database.get_task(task_id)
        self.window.set_progress("完成" if task.status.value == "completed" else task.status.value, 100)
        self.window.append_log(f"批量任务结束：{task.status.value}")
        self.window.set_history(self.application.database.list_tasks())
        self.window.set_busy(False)

    @Slot()
    def pause_batch(self) -> None:
        if self._active_queue is not None and self._active_task_id is not None:
            self._active_queue.pause(self._active_task_id)
            self.window.set_progress("已暂停", self.window.progress_bar.value())

    @Slot()
    def resume_batch(self) -> None:
        if self._active_queue is not None and self._active_task_id is not None:
            resumed = self._active_queue.resume(self._active_task_id)
            if not resumed:
                task = self.application.database.get_task(self._active_task_id)
                is_completed = task.status.value == "completed"
                self.window.set_progress(
                    "完成" if is_completed else task.status.value,
                    100 if is_completed else self.window.progress_bar.value(),
                )
                self.window.append_log("任务已经完成，没有需要继续处理的页面")
                self.window.set_history(self.application.database.list_tasks())
                self.window.set_busy(False)
                return
            self.window.set_progress("继续处理中", self.window.progress_bar.value())

    @Slot()
    def cancel_batch(self) -> None:
        if self._active_queue is not None and self._active_task_id is not None:
            self._active_queue.cancel(self._active_task_id)
            self.window.set_progress("正在取消", self.window.progress_bar.value())

    @Slot()
    def quit_application(self) -> None:
        QApplication.instance().quit()

    @Slot()
    def retry_failed_page(self) -> None:
        if self._active_queue is None or self._active_task_id is None:
            self.window.show_error("当前没有可重试的任务")
            return
        if self._queue_running:
            self.window.show_error("当前任务仍在处理中，请等待完成后再重试失败页面")
            return
        row = self.window.queue_table.currentRow()
        if not 0 <= row < len(self._active_page_ids):
            self.window.show_error("请先在队列表格中选择失败页面")
            return
        page_id = self._active_page_ids[row]
        self._active_queue.retry_page(page_id)
        self.window.update_queue_page(row, "等待重试")
        self.window.set_busy(True)

        async def operation(_progress):
            await self._active_queue.run(self._active_task_id)
            return self._active_task_id

        self._start_worker(operation, on_completed=self._batch_completed, on_failed=self._translation_failed)

    def _metadata_paths(self) -> list[Path]:
        if self._active_task_id is None:
            return []
        task = self.application.database.get_task(self._active_task_id)
        metadata_dir = Path(task.output_dir) / "metadata"
        return [
            metadata_dir / f"{Path(page.source_path).stem}.json"
            for page in self.application.database.list_task_pages(self._active_task_id)
            if (metadata_dir / f"{Path(page.source_path).stem}.json").is_file()
        ]

    def export_text(self, format_name: str) -> None:
        metadata_paths = self._metadata_paths()
        if not metadata_paths:
            self.window.show_error("当前任务还没有可导出的OCR/翻译结果")
            return
        suffix = ".txt" if format_name == "txt" else ".json"
        path, _ = QFileDialog.getSaveFileName(
            self.window, "导出翻译文本", str(self.window.paths.output_root / f"翻译文本{suffix}"),
            "文本文件 (*.txt)" if format_name == "txt" else "JSON文件 (*.json)",
        )
        if not path:
            return
        service = TextExchangeService()
        if format_name == "txt":
            service.export_txt(metadata_paths, Path(path))
        else:
            service.export_json(metadata_paths, Path(path))
        self.window.append_log(f"已导出：{path}")

    @Slot()
    def import_text_and_rerender(self) -> None:
        metadata_paths = self._metadata_paths()
        if not metadata_paths:
            self.window.show_error("当前任务没有可更新的页面JSON")
            return
        path, _ = QFileDialog.getOpenFileName(
            self.window, "导入人工修改的翻译", str(self.window.paths.output_root),
            "翻译文件 (*.txt *.json)",
        )
        if not path:
            return
        service = TextExchangeService()
        try:
            if Path(path).suffix.lower() == ".txt":
                updates = service.import_txt(Path(path))
                for metadata_path in metadata_paths:
                    service.apply_translations(metadata_path, updates)
            else:
                document = service.import_json(Path(path))
                if len(document["pages"]) != len(metadata_paths):
                    raise ValueError("导入JSON的页面数量与当前任务不一致")
                for metadata_path, page_document in zip(metadata_paths, document["pages"], strict=True):
                    current = json.loads(metadata_path.read_text(encoding="utf-8"))
                    current["regions"] = page_document.get("regions", [])
                    metadata_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as error:
            self.window.show_error(redact_text(str(error)))
            return
        self.window.set_busy(True)

        async def operation(progress):
            latest = None
            for index, metadata_path in enumerate(metadata_paths):
                latest = await self.application.rerender_metadata(metadata_path)
                self.page_preview_ready.emit(str(latest))
                progress(f"重新渲染第{index + 1}/{len(metadata_paths)}页", round((index + 1) / len(metadata_paths) * 100))
            return latest

        self._start_worker(
            operation,
            on_completed=lambda _result: self.window.set_busy(False),
            on_failed=self._translation_failed,
        )

    @Slot()
    def export_zip(self) -> None:
        if self._active_task_id is None:
            self.window.show_error("当前没有可导出的任务")
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window, "导出译图ZIP", str(self.window.paths.output_root / "译图.zip"), "ZIP压缩包 (*.zip)"
        )
        if path:
            self.application.export_task_zip(self._active_task_id, Path(path))
            self.window.append_log(f"译图ZIP已导出：{path}")

    def _translation_completed(self, outcome: ProcessingOutcome) -> None:
        translated_path = outcome.result.artifacts.translated
        self.window.set_result_preview(translated_path)
        self.window.set_progress("完成", 100)
        self.window.append_log(f"处理完成：{translated_path}")
        self.window.set_busy(False)

    def _translation_failed(self, message: str) -> None:
        self._queue_running = False
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
        thread.finished.connect(lambda: self._workers.pop(thread, None))
        thread.finished.connect(lambda: self._threads.discard(thread))
        self._threads.add(thread)
        self._workers[thread] = worker
        thread.start()
