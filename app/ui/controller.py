from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Callable, Coroutine

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from app.security.redaction import redact_text
from app.services.application import ProcessingOutcome, TranslationApplication
from app.services.output_paths import batch_requires_name, ensure_output_writable, resolve_batch_output
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


class _WorkerCallbacks(QObject):
    """Keep Python completion closures on the GUI thread via QObject slots."""

    def __init__(self, completed, failed, finished, parent: QObject) -> None:
        super().__init__(parent)
        self._completed = completed
        self._failed = failed
        self._finished = finished

    @Slot(object)
    def completed(self, result) -> None:
        self._completed(result)

    @Slot(str)
    def failed(self, message: str) -> None:
        self._failed(message)

    @Slot()
    def finished(self) -> None:
        self._finished()


class MainController(QObject):
    """界面事件与应用服务的桥接层，管理任务队列和后台线程。"""

    page_preview_ready = Signal(str)
    page_status_ready = Signal(int, str, str)
    page_progress_ready = Signal(int, int, int, str)
    task_prepared = Signal(str, object, object)
    page_record_ready = Signal(object)

    def __init__(self, application: TranslationApplication, window: MainWindow) -> None:
        super().__init__(window)
        self.application = application
        self.window = window
        self._threads: set[QThread] = set()
        self._workers: dict[QThread, AsyncWorker] = {}
        self._worker_callbacks: dict[QThread, _WorkerCallbacks] = {}
        self._active_queue = None
        self._active_task_id: str | None = None
        self._queue_running = False
        self._quitting = False
        self._active_page_ids: list[str] = []
        self._progress_task_id = None
        self._page_records = {}
        self._progress_counts = {}
        self._pending_records = {}
        self._task_state = "pending"
        self._pending_queue_action = None
        self._queue_control_lock = threading.RLock()
        self._progress_stage = "准备"
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._flush_page_updates)
        self._load_config()
        if hasattr(application, "load_render_settings"):
            window.apply_preset_values({"render_settings": application.load_render_settings()})
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
        window.editor_open_requested.connect(self.edit_selected_page)
        window.render_save_requested.connect(self.save_render_settings)
        window.page_editor.open_requested.connect(self.open_editor_file)
        window.page_editor.render_requested.connect(self.save_and_render_editor)
        if hasattr(window.page_editor, "next_review_page_requested"):
            window.page_editor.next_review_page_requested.connect(self.open_next_review_page)
        window.pause_requested.connect(self.pause_batch)
        window.resume_requested.connect(self.resume_batch)
        window.cancel_requested.connect(self.cancel_batch)
        window.exit_requested.connect(self.quit_application)
        self.page_preview_ready.connect(lambda path: self.window.set_result_preview(Path(path)))
        self.page_status_ready.connect(self.window.update_queue_page)
        self.page_progress_ready.connect(self._show_batch_progress)
        self.task_prepared.connect(self._initialize_task_view, Qt.ConnectionType.QueuedConnection)
        self.page_record_ready.connect(self._record_page_update, Qt.ConnectionType.QueuedConnection)

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
            self._refresh_history()

    def _refresh_history(self) -> None:
        self.window.set_history(self.application.database.list_tasks(), self.application.database.task_page_counts())

    @staticmethod
    def _status_text(status) -> str:
        return {"pending": "等待恢复", "running": "处理中", "paused": "已暂停", "completed": "完成",
                "failed": "失败", "cancelled": "已取消"}.get(str(status), str(status))

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
            try:
                self.application.save_library_content(
                    "replacement", "当前替换规则", self.window.replacement_editor.toPlainText()
                )
                self.window.append_log("替换规则已保存")
            except Exception as error:
                self.window.show_error(redact_text(str(error)))

    @Slot()
    def save_render_settings(self) -> None:
        try:
            self.application.save_render_settings(self._current_options())
            self.window.append_log("字体设置已保存，将用于新任务和重绘。")
        except Exception as error:
            self.window.show_error(redact_text(str(error)))

    @Slot()
    def save_preset(self) -> None:
        if not hasattr(self.application, "save_library_content"):
            return
        name = self.window.preset_name.text().strip()
        if not name:
            self.window.show_error("请输入预设名称")
            return
        try:
            rendering = self._current_options().render_settings()
        except ValueError as error:
            self.window.show_error(str(error))
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
                "render_settings": rendering,
            },
            ensure_ascii=False,
        )
        entry_id = self.application.save_library_content("preset", name, payload)
        self._load_config()
        index = self.window.preset_list.findData(entry_id)
        if index >= 0:
            self.window.preset_list.setCurrentIndex(index)
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
        if self._threads or self._quitting:
            self.window.show_error("请等待当前处理安全结束")
            return
        sources = self.window.selected_sources()
        output_text = self.window.output_edit.text().strip()
        if not sources or any(not source.exists() for source in sources):
            self.window.show_error("请选择有效的漫画图片、文件夹、ZIP、CBZ或EPUB。")
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
            ensure_output_writable(output_dir)
        except (ValueError, OSError) as error:
            self.window.show_error(str(error))
            return
        source_path = sources[0]
        if source_path.is_file() and source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            self.window.set_original_preview(source_path)
        try:
            options = self._current_options()
        except ValueError as error:
            self.window.show_error(str(error))
            return
        self.window.set_busy(True)
        self.window.set_progress("准备", 0)
        self.window.append_log(f"开始处理：{source_path.name}")

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
            if options.translate and hasattr(self.application, "validate_translation_configuration"):
                self.application.validate_translation_configuration()
        except Exception as error:
            self._translation_failed(redact_text(str(error)))
            return
        selection = self._pipeline_selection()
        self._active_queue = None
        self._active_task_id = None
        self._active_page_ids = []
        self._pending_queue_action = None
        self._queue_running = True
        self.window.set_queue_pages([])

        async def operation(progress):
            try:
                progress("正在导入漫画与创建任务", 0)
                batch = self.application.create_batch_task(sources, output_dir)
                return await self._execute_task(batch.task_id, options, selection, progress)
            finally:
                self.application.database.close()

        self._start_worker(operation, on_completed=self._batch_completed, on_failed=self._translation_failed)

    def _pipeline_selection(self) -> dict:
        return {
            "ocr_engine": str(self.window.ocr_engine.currentData()),
            "inpainting_engine": str(self.window.inpainting_engine.currentData()),
            "high_accuracy_ocr": self.window.high_accuracy_ocr.isChecked(),
            "max_concurrent_pages": self.window.concurrent_pages.value(),
        }

    async def _execute_task(self, task_id, options, selection, progress):
        """Runs entirely in the worker; never read or mutate Qt widgets here."""
        self._active_task_id = task_id
        progress("准备识别与擦字引擎", 0)
        pages = self.application.database.list_task_pages(task_id)

        def changed(_task_id, page, *_stage):
            self.page_record_ready.emit(page)

        queue = self.application.build_task_queue(
            options, **selection, on_page_completed=changed,
            on_page_failed=changed, on_page_progress=changed,
        )
        snapshot = {
            "source_language": options.source_language, "target_language": options.target_language,
            "translate": options.translate, "review_threshold": options.review_threshold,
            "render_settings": options.render_settings(),
            "ocr_engine": selection["ocr_engine"], "inpainting_engine": selection["inpainting_engine"],
            "high_accuracy_ocr": selection["high_accuracy_ocr"],
            "concurrent_pages": selection["max_concurrent_pages"],
        }
        self.application.database.set_setting(f"task.{task_id}.options", json.dumps(snapshot, ensure_ascii=False))
        # Bind the queue and apply the latest preparation intent atomically.
        # A fast resume must not be followed by an older captured pause.
        with self._queue_control_lock:
            self._active_queue = queue
            action = self._pending_queue_action
            if action in {"pause", "cancel", "stop"}:
                getattr(queue, action)(task_id)
        self.task_prepared.emit(task_id, pages, queue)
        await queue.run(task_id)
        return task_id

    def _current_options(self) -> PipelineOptions:
        return PipelineOptions(
            source_language=str(self.window.source_language.currentData()),
            target_language=str(self.window.target_language.currentData()),
            review_threshold=self.window.review_threshold.value() / 100,
            translate=self.window.translate_checkbox.isChecked(),
            font_path=self.window.font_path.text().strip() or None,
            min_font_size=self.window.min_font_size.value(),
            max_font_size=self.window.max_font_size.value(),
        )

    def _run_persisted_task(self, task_id: str, options: PipelineOptions) -> None:
        self._active_task_id = task_id
        self._active_queue = None
        self._pending_queue_action = None
        self._queue_running = True
        selection = self._pipeline_selection()

        async def operation(progress):
            try:
                return await self._execute_task(task_id, options, selection, progress)
            finally:
                self.application.database.close()

        self._start_worker(operation, on_completed=self._batch_completed, on_failed=self._translation_failed)

    @Slot(str, object, object)
    def _initialize_task_view(self, task_id, pages, queue) -> None:
        if not self._queue_running or task_id != self._active_task_id:
            return
        self._active_queue = queue
        self._load_progress_snapshot(task_id, pages)
        action = self._pending_queue_action
        self._task_state = "paused" if action in {"pause", "stop"} else "cancelled" if action == "cancel" else "running"
        self._render_progress_summary("准备")

    def _load_progress_snapshot(self, task_id, pages) -> None:
        self._progress_timer.stop()
        self._pending_records.clear()
        self._progress_task_id = task_id
        self._active_task_id = task_id
        self._active_page_ids = [page.id for page in pages]
        self._page_records = {page.id: page for page in pages}
        self._progress_counts = {}
        self.window.set_queue_pages([Path(page.source_path) for page in pages])
        for page in pages:
            key = page.status.value
            self._progress_counts[key] = self._progress_counts.get(key, 0) + 1
            self._paint_page_row(page)

    def _paint_page_row(self, page) -> None:
        self.window.update_queue_page(page.page_index,
            page.stage if page.status.value == "running" else self._status_text(page.status), page.error or "")

    @Slot(object)
    def _record_page_update(self, page) -> None:
        if page.task_id != self._active_task_id or not self._queue_running:
            return
        previous = self._pending_records.get(page.id, self._page_records.get(page.id))
        if previous is not None and self._stale_page_update(previous, page):
            return
        self._pending_records[page.id] = page
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def _flush_page_updates(self) -> None:
        self._progress_timer.stop()
        updates = list(self._pending_records.values())
        self._pending_records.clear()
        latest_preview = None
        for page in updates:
            self._apply_page_update(page)
            if page.status.value == "completed" and page.result_path:
                latest_preview = page.result_path
        if latest_preview:
            self.window.set_result_preview(Path(latest_preview))
        if updates:
            self._render_progress_summary(updates[-1].stage)

    def _apply_page_update(self, page) -> None:
        old = self._page_records.get(page.id)
        if old is None or self._stale_page_update(old, page):
            return
        old_status, new_status = old.status.value, page.status.value
        if old_status != new_status:
            self._progress_counts[old_status] = self._progress_counts.get(old_status, 0) - 1
            self._progress_counts[new_status] = self._progress_counts.get(new_status, 0) + 1
        self._page_records[page.id] = page
        if old != page:
            self._paint_page_row(page)

    @staticmethod
    def _stale_page_update(old, new) -> bool:
        if new.attempt_count < old.attempt_count:
            return True
        if (old.status.value == new.status.value == "running" and new.attempt_count == old.attempt_count
                and new.progress < old.progress):
            return True
        return (old.status.value in {"completed", "failed", "cancelled"}
                and new.status.value == "running" and new.attempt_count == old.attempt_count)

    def _render_progress_summary(self, stage: str) -> None:
        completed = self._progress_counts.get("completed", 0)
        failed = self._progress_counts.get("failed", 0)
        total = len(self._page_records)
        label = stage if self._task_state == "running" else self._status_text(self._task_state)
        self.window.set_progress(f"{label} · 完成{completed}/{total}页 · 失败{failed}页", completed * 100 // max(total, 1))
        self.window.page_counter.setText(f"完成 {completed} / {total} 页 · 失败 {failed} 页")

    @Slot()
    def resume_history_task(self) -> None:
        if self._threads:
            self.window.show_error("请等待当前处理结束后再恢复历史任务")
            return
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
        if all(page.status.value == "completed" for page in pages):
            self._active_task_id = task_id
            self._active_queue = None
            self._active_page_ids = [page.id for page in pages]
            self.window.set_queue_pages([Path(page.source_path) for page in pages])
            self._show_batch_progress(0, len(pages), 100, "完成")
            if pages[-1].result_path and Path(pages[-1].result_path).is_file():
                self.window.set_result_preview(Path(pages[-1].result_path))
            self.window.append_log("此任务已完成，已载入结果供编辑和导出；没有调用翻译服务")
            return
        self.window.output_edit.setText(task.output_dir)
        self.window.set_busy(True)
        self.window.set_progress("恢复任务", 0)
        self.window.append_log(f"恢复历史任务：{task.id[:10]}")
        try:
            saved_options = self.application.database.get_setting(f"task.{task_id}.options")
            if saved_options:
                snapshot = json.loads(saved_options)
                self.window.apply_preset_values(snapshot)
                self.window.translate_checkbox.setChecked(bool(snapshot["translate"]))
                self.window.review_threshold.setValue(round(float(snapshot["review_threshold"]) * 100))
                self.window.append_log("已恢复该任务的识别、翻译开关与字体设置；术语表和API使用当前配置")
            else:
                self.window.append_log("旧任务未保存处理设置，本次按当前面板设置恢复")
            if self.window.translate_checkbox.isChecked():
                self.application.validate_translation_configuration()
            options = self._current_options()
            ensure_output_writable(Path(task.output_dir))
            self.application.database.reset_cancelled_pages(task_id)
            self._run_persisted_task(task_id, options)
        except Exception as error:
            self._translation_failed(redact_text(str(error)))

    @Slot()
    def edit_selected_page(self) -> None:
        if self._queue_running:
            self.window.show_error("请等待当前任务结束后再编辑页面。")
            return
        row = self.window.queue_table.currentRow()
        if row < 0 and len(self._active_page_ids) == 1:
            row = 0
        if self._active_task_id is None or not 0 <= row < len(self._active_page_ids):
            self.window.show_error("请先在队列表格中选择已处理的页面。")
            return
        page = self.application.database.get_page(self._active_page_ids[row])
        task = self.application.database.get_task(self._active_task_id)
        self._open_editor_metadata(Path(task.output_dir) / "metadata" / f"{Path(page.source_path).stem}.json")

    @Slot()
    def open_editor_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.window, "打开结果 metadata 目录中的页面JSON", str(self.window.paths.output_root),
            "页面JSON (*.json)",
        )
        if path:
            self._open_editor_metadata(Path(path))

    def _open_editor_metadata(self, metadata_path: Path) -> None:
        image = metadata_path.parent.parent / "inpainted" / f"{metadata_path.stem}_inpainted.png"
        try:
            original, available = self.application.load_page_source(metadata_path)
            self.window.page_editor.load_page(image, metadata_path, original_image=original, original_available=available)
            self.window.tabs.setCurrentWidget(self.window.page_editor)
        except Exception as error:
            self.window.show_error(f"无法打开编辑页面：{redact_text(str(error))}")

    @Slot()
    def save_and_render_editor(self) -> None:
        editor = self.window.page_editor
        if editor.metadata_path is None:
            self.window.show_error("请先打开一个已处理的页面。")
            return
        if self._threads:
            self.window.show_error("请等待当前处理结束后再重绘页面。")
            return
        try:
            options = self._current_options()
            editor.save_page()
        except Exception as error:
            self.window.show_error(redact_text(str(error)))
            return
        metadata_path = editor.metadata_path
        engine = str(self.window.inpainting_engine.currentData())
        self._set_file_busy()

        async def operation(progress):
            progress("重绘译图", 50)
            return await self.application.rerender_metadata(metadata_path, render_options=options, inpainting_engine=engine)

        def completed(path):
            editor.show_rendered_preview(path)
            self.window.set_result_preview(path)
            self.window.set_progress("重绘完成", 100)
            self.window.set_busy(False)
            self.window.append_log(f"编辑结果已重绘：{path.name}")

        self._start_worker(operation, on_completed=completed, on_failed=self._translation_failed)

    def _show_batch_progress(self, page_index: int, total: int, page_progress: int, stage: str) -> None:
        # Compatibility entry point for existing callers; normal queue events
        # carry PageRecord snapshots and incur no UI-thread database reads.
        if self._active_task_id is None:
            return
        if self._progress_task_id != self._active_task_id:
            pages = self.application.database.list_task_pages(self._active_task_id)
            self._load_progress_snapshot(self._active_task_id, pages)
            self._task_state = self.application.database.get_task(self._active_task_id).status.value
        elif 0 <= page_index < len(self._active_page_ids):
            page = self.application.database.get_page(self._active_page_ids[page_index])
            self._apply_page_update(page)
        self._render_progress_summary(stage)

    def _batch_completed(self, task_id: str) -> None:
        self._flush_page_updates()
        self._queue_running = False
        task = self.application.database.get_task(task_id)
        pages = self.application.database.list_task_pages(task_id)
        if self._progress_task_id != task_id or len(pages) != len(self._page_records):
            self._load_progress_snapshot(task_id, pages)
        else:
            for page in pages:
                self._apply_page_update(page)
        self._task_state = task.status.value
        self._render_progress_summary(self._status_text(task.status))
        self.window.append_log(f"批量任务结束：{self._status_text(task.status)}")
        self._refresh_history()
        self.window.set_busy(False)

    @Slot()
    def pause_batch(self) -> None:
        if not self._queue_running or self._quitting or self._pending_queue_action in {"cancel", "stop"}:
            return
        with self._queue_control_lock:
            self._pending_queue_action = "pause"
            self._task_state = "paused"
            if self._active_queue is not None and self._active_task_id is not None:
                self._active_queue.pause(self._active_task_id)
        self.window.set_progress("正在暂停：当前步骤结束后等待", self.window.progress_bar.value())

    @Slot()
    def resume_batch(self) -> None:
        if not self._queue_running:
            return
        if self._quitting or self._pending_queue_action in {"cancel", "stop"}:
            self.window.append_log("正在安全停止，请等待结束后从任务历史恢复")
            return
        with self._queue_control_lock:
            self._pending_queue_action = None
            self._task_state = "running"
            if self._active_queue is not None and self._active_task_id is not None:
                if not self._active_queue.resume(self._active_task_id):
                    return
        self.window.set_progress("继续处理中", self.window.progress_bar.value())

    @Slot()
    def cancel_batch(self) -> None:
        if not self._queue_running:
            return
        with self._queue_control_lock:
            self._pending_queue_action = "cancel"
            self._task_state = "cancelled"
            if self._active_queue is not None and self._active_task_id is not None:
                self._active_queue.cancel(self._active_task_id)
        self.window.set_progress("正在取消：保留已经完成的页面", self.window.progress_bar.value())

    @Slot()
    def quit_application(self) -> None:
        self._quitting = True
        if self._threads:
            if self._queue_running:
                with self._queue_control_lock:
                    self._pending_queue_action = "stop"
                    self._task_state = "paused"
                    if self._active_queue is not None and self._active_task_id is not None:
                        self._active_queue.stop(self._active_task_id)
            self.window.show_from_tray()
            self.window.set_busy(True)
            self.window.set_progress("正在安全退出：等待当前步骤结束，未完成页面下次可恢复", self.window.progress_bar.value())
            return
        self.window._allow_quit = True
        self.window.tray_icon.hide()
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
        try:
            self._active_queue.retry_page(page_id)
        except ValueError as error:
            self.window.show_error(str(error))
            return
        self.window.update_queue_page(row, "等待重试")
        self.window.set_busy(True)
        self._pending_queue_action = None
        self._task_state = "running"
        self._apply_page_update(self.application.database.get_page(page_id))

        async def operation(_progress):
            await self._active_queue.run(self._active_task_id, page_ids=[page_id])
            return self._active_task_id

        self._start_worker(operation, on_completed=self._batch_completed, on_failed=self._translation_failed)
        self._queue_running = True

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
        if self._threads:
            self.window.show_error("请等待当前处理结束后再导出")
            return
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
        self._set_file_busy()

        async def operation(progress):
            progress("正在导出翻译文本", 0)
            service = TextExchangeService()
            if format_name == "txt":
                service.export_txt(metadata_paths, Path(path))
            else:
                service.export_json(metadata_paths, Path(path))
            return Path(path)

        self._start_worker(operation, on_completed=self._file_exported, on_failed=self._translation_failed)

    def _file_exported(self, path: Path) -> None:
        self.window.set_progress("导出完成", 100)
        self.window.set_busy(False)
        self.window.append_log(f"已导出：{path}")

    def _set_file_busy(self) -> None:
        self.window.set_busy(True)
        self.window.pause_button.setEnabled(False)
        self.window.resume_button.setEnabled(False)
        self.window.cancel_button.setEnabled(False)

    @Slot()
    def import_text_and_rerender(self) -> None:
        if self._threads:
            self.window.show_error("请等待当前任务结束后再导入译文")
            return
        metadata_paths = self._metadata_paths()
        if not metadata_paths:
            self.window.show_error("当前任务没有可更新的页面JSON")
            return
        path, _ = QFileDialog.getOpenFileName(
            self.window, "导入人工修改的翻译", str(self.window.paths.output_root), "翻译文件 (*.txt *.json)",
        )
        if not path:
            return
        engine = str(self.window.inpainting_engine.currentData())
        self._set_file_busy()

        async def operation(progress):
            try:
                progress("正在校验与导入人工译文", 0)
                result = TextExchangeService().import_with_result(metadata_paths, Path(path))
                latest = None
                for index, metadata_path in enumerate(result.changed_paths):
                    latest = await self.application.rerender_metadata(metadata_path, inpainting_engine=engine)
                    progress(f"重新渲染第{index + 1}/{len(result.changed_paths)}个改动页", round((index + 1) / len(result.changed_paths) * 100))
                return result, latest
            finally:
                self.application.database.close()

        def completed(outcome):
            result, latest = outcome
            if latest is not None:
                self.window.set_result_preview(latest)
            editor = self.window.page_editor
            if editor.metadata_path in result.changed_paths:
                previous = self.window.tabs.currentWidget()
                self._open_editor_metadata(editor.metadata_path)
                self.window.tabs.setCurrentWidget(previous)
            self.window.set_busy(False)
            self.window.set_progress("导入并重绘完成" if result.changed_paths else "内容没有变化，无需重绘", 100)
            self.window.append_log(f"人工译文已校验：修改{result.changed_regions}个文本框，只重绘{len(result.changed_paths)}页")

        self._start_worker(operation, on_completed=completed, on_failed=self._translation_failed)

    @Slot()
    def export_zip(self) -> None:
        if self._threads:
            self.window.show_error("请等待当前处理结束后再导出")
            return
        if self._active_task_id is None:
            self.window.show_error("当前没有可导出的任务")
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window, "导出译图ZIP", str(self.window.paths.output_root / "译图.zip"), "ZIP压缩包 (*.zip)"
        )
        if not path:
            return
        task_id = self._active_task_id
        self._set_file_busy()

        async def operation(progress):
            try:
                progress("正在打包译图", 0)
                return self.application.export_task_zip(task_id, Path(path))
            finally:
                self.application.database.close()

        self._start_worker(operation, on_completed=self._file_exported, on_failed=self._translation_failed)

    @Slot()
    def open_next_review_page(self) -> None:
        if self._threads:
            self.window.append_log("请等待当前工作结束后再跳转检查")
            return
        editor = self.window.page_editor
        paths = self._metadata_paths()
        if not paths or editor.metadata_path is None:
            editor.page_status.setText("当前没有其他待检查页面")
            return
        try:
            editor.save_page()
        except Exception as error:
            self.window.show_error(redact_text(str(error)))
            return
        start = paths.index(editor.metadata_path) + 1 if editor.metadata_path in paths else 0
        ordered = paths[start:] + paths[:start]
        current = editor.metadata_path
        self._set_file_busy()
        editor.setEnabled(False)
        editor.page_status.setText("正在寻找下一处待检查区域…")

        async def operation(progress):
            for path in ordered:
                if path == current:
                    continue
                document = TextExchangeService._load_metadata(path)
                if any((region.get("needs_review") or region.get("metadata", {}).get("render_warning"))
                       and region.get("metadata", {}).get("review_decision") != "rejected"
                       for region in document["regions"]):
                    return path
            return None

        def completed(path):
            editor.setEnabled(True)
            self.window.set_busy(False)
            if path is None:
                editor.page_status.setText("所有已处理页面都没有待检查区域")
            else:
                self._open_editor_metadata(path)
                editor.next_review_region()

        def failed(message):
            editor.setEnabled(True)
            self._translation_failed(message)

        self._start_worker(operation, on_completed=completed, on_failed=failed)

    def _translation_completed(self, outcome: ProcessingOutcome) -> None:
        translated_path = outcome.result.artifacts.translated
        self.window.set_result_preview(translated_path)
        self.window.set_progress("完成", 100)
        self.window.append_log(f"处理完成：{translated_path}")
        self.window.set_busy(False)

    def _translation_failed(self, message: str) -> None:
        self._progress_timer.stop()
        self._pending_records.clear()
        self._queue_running = False
        self.window.set_progress("失败", 0)
        self.window.append_log(f"处理失败：{message}")
        self.window.set_busy(False)
        self.window.show_error(message)

    def _start_worker(self, operation, *, on_completed, on_failed) -> None:
        thread = QThread(self)
        worker = AsyncWorker(operation)
        callbacks = _WorkerCallbacks(
            on_completed, on_failed, lambda: self._finish_worker(thread), self,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.window.set_progress, Qt.ConnectionType.QueuedConnection)
        worker.completed.connect(callbacks.completed, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(callbacks.failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(callbacks.finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        self._threads.add(thread)
        self._workers[thread] = worker
        self._worker_callbacks[thread] = callbacks
        thread.start()

    def _finish_worker(self, thread: QThread) -> None:
        self._workers.pop(thread, None)
        self._threads.discard(thread)
        callbacks = self._worker_callbacks.pop(thread, None)
        if callbacks is not None:
            callbacks.deleteLater()
        if self._quitting and not self._threads:
            self.quit_application()
