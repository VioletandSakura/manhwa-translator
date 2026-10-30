from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidgetItem,
)

from app.paths import AppPaths
from app.windows_identity import APPLICATION_NAME
from app.ui.page_editor import PageEditorWidget
from app.ui.workspace import WorkspaceLayouts


class MainWindow(WorkspaceLayouts, QMainWindow):
    """主窗口：翻译、API、OCR、术语、历史与日志等全部设置页。"""

    start_requested = Signal()
    api_save_requested = Signal()
    api_test_requested = Signal()
    api_models_requested = Signal()
    api_revoke_requested = Signal()
    api_delete_requested = Signal()
    library_save_requested = Signal()
    replacement_save_requested = Signal()
    preset_save_requested = Signal()
    preset_apply_requested = Signal()
    history_resume_requested = Signal()
    retry_requested = Signal()
    export_txt_requested = Signal()
    export_json_requested = Signal()
    import_text_requested = Signal()
    export_zip_requested = Signal()
    editor_open_requested = Signal()
    render_save_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()
    exit_requested = Signal()

    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        # zh_MO uses Suzhou numerals in Qt; keep numeric controls readable.
        self.setLocale(QLocale("zh_CN"))
        self.paths = paths
        self._selected_sources: list[Path] = []
        self.setWindowTitle(APPLICATION_NAME)
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1440, max(960, available.width() - 80)) if available else 1440,
                    min(900, max(640, available.height() - 80)) if available else 900)
        self.setMinimumSize(960, 640)
        self.setWindowIcon(self._app_icon())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_translation_tab(), "开始翻译")
        self.tabs.addTab(self._build_api_tab(), "API管理")
        self.tabs.addTab(self._build_ocr_tab(), "OCR设置")
        self.tabs.addTab(self._build_detection_tab(), "检测与擦字设置")
        self.tabs.addTab(self._build_render_tab(), "渲染与字体")
        self.tabs.addTab(self._build_library_tab(), "提示词与术语表")
        self.tabs.addTab(self._build_replacement_tab(), "替换规则")
        self.tabs.addTab(self._build_preset_tab(), "预设管理")
        self.tabs.addTab(self._build_history_tab(), "任务历史")
        self.tabs.addTab(self._build_log_tab(), "日志与诊断")
        self.page_editor = PageEditorWidget()
        self.tabs.addTab(self.page_editor, "可视化编辑器")
        self._build_shell()
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(0)
        for spin in self.findChildren(QSpinBox):
            spin.setMaximumWidth(160)
        self._apply_style()
        self.set_busy(False)
        self._allow_quit = False
        self._tray_hint_shown = False
        self._setup_tray()

    def _setup_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_icon.setToolTip(APPLICATION_NAME)
        menu = QMenu(self)
        show_action = menu.addAction("显示主窗口")
        show_action.triggered.connect(self.show_from_tray)
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.request_quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.messageClicked.connect(self.show_from_tray)

    def show_from_tray(self) -> None:
        if self.windowState() & Qt.WindowState.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        self.exit_requested.emit()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_quit:
            event.accept()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.request_quit()
            return
        event.ignore()
        self.hide()
        self.tray_icon.show()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray_icon.showMessage(
                APPLICATION_NAME,
                "窗口已隐藏，任务仍在后台运行；点击托盘图标可恢复，右键可退出。",
                self.windowIcon(),
                3000,
            )

    @staticmethod
    def _app_icon() -> QIcon:
        from app.ui.theme import icon
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c34b2b"))
        painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
        painter.drawPixmap(12, 12, icon("book", "#ffffff", 40).pixmap(40, 40))
        painter.end()
        return QIcon(pixmap)

    def _choose_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择字体", str(self.paths.fonts_dir), "字体文件 (*.ttf *.otf *.ttc)")
        if path:
            self.font_path.setText(path)

    def _choose_source(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择漫画图片、ZIP、CBZ或EPUB",
            str(self.paths.project_root),
            "漫画输入 (*.png *.jpg *.jpeg *.webp *.bmp *.zip *.cbz *.epub)",
        )
        if paths:
            self._selected_sources = [Path(path) for path in paths]
            self.source_edit.setText("；".join(paths))
            if self._selected_sources[0].suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                self.set_original_preview(self._selected_sources[0])

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择漫画文件夹", str(self.paths.project_root))
        if path:
            self._selected_sources = [Path(path)]
            self.source_edit.setText(path)

    def selected_sources(self) -> list[Path]:
        if self._selected_sources:
            return list(self._selected_sources)
        text = self.source_edit.text().strip()
        return [Path(text)] if text else []

    def set_queue_pages(self, paths: list[Path]) -> None:
        self.queue_table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            self.queue_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            self.queue_table.setItem(row, 1, name_item)
            self.queue_table.setItem(row, 2, QTableWidgetItem("等待"))
            self.queue_table.setItem(row, 3, QTableWidgetItem(""))

    def update_queue_page(self, index: int, status: str, error: str = "") -> None:
        if 0 <= index < self.queue_table.rowCount():
            item = QTableWidgetItem(status)
            item.setForeground(QColor("#24734d" if status == "完成" else "#b43d34" if status == "失败" else "#626a77"))
            self.queue_table.setItem(index, 2, item)
            detail = QTableWidgetItem(error)
            detail.setToolTip(error)
            self.queue_table.setItem(index, 3, detail)

    def set_history(self, tasks, counts=None) -> None:
        tasks = list(tasks)
        self._history_tasks = tasks
        self.history_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            tally = (counts or {}).get(task.id, {})
            total = sum(tally.values())
            summary = (f"完成{tally.get('completed', 0)}/{total} · 失败{tally.get('failed', 0)}"
                       f" · 取消{tally.get('cancelled', 0)} · 待处理{tally.get('pending', 0) + tally.get('running', 0)}")
            status = {"pending": "待处理", "running": "处理中", "paused": "已暂停，可恢复", "completed": "完成",
                      "failed": "有失败页", "cancelled": "已取消，可恢复"}.get(task.status.value, task.status.value)
            try:
                created = datetime.fromisoformat(task.created_at).astimezone().strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                created = task.created_at
            values = [task.id[:10], Path(task.source_path).name or task.source_path, status, summary,
                      created, Path(task.output_dir).name or task.output_dir]
            details = [task.id, task.source_path, status, summary, task.created_at, task.output_dir]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(details[column])
                self.history_table.setItem(row, column, item)

    def selected_history_task_id(self) -> str | None:
        row = self.history_table.currentRow()
        if 0 <= row < len(getattr(self, "_history_tasks", [])):
            return self._history_tasks[row].id
        return None

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def apply_preset_values(self, payload: dict[str, object]) -> None:
        api = payload.get("api", {})
        if isinstance(api, dict):
            text_fields = {
                "id": self.api_channel_id,
                "service_name": self.api_service_name,
                "base_url": self.api_base_url,
            }
            for key, widget in text_fields.items():
                if key in api:
                    widget.setText(str(api[key]))
            if "model" in api:
                self.api_model.setCurrentText(str(api["model"]))
            spin_fields = {
                "timeout_seconds": self.api_timeout,
                "retries": self.api_retries,
                "max_concurrency": self.api_max_concurrency,
                "rpm_limit": self.api_rpm_limit,
                "priority": self.api_priority,
            }
            for key, widget in spin_fields.items():
                if key in api:
                    widget.setValue(int(api[key]))
            if "enabled" in api:
                self.api_enabled.setChecked(bool(api["enabled"]))
            self._select_combo_data(self.api_strategy, api.get("strategy"))
        self._select_combo_data(self.ocr_engine, payload.get("ocr_engine"))
        high_accuracy = payload.get("high_accuracy_ocr")
        if isinstance(high_accuracy, bool):
            self.high_accuracy_ocr.setChecked(high_accuracy)
        if "concurrent_pages" in payload:
            self.concurrent_pages.setValue(int(payload["concurrent_pages"]))
        self._select_combo_data(self.inpainting_engine, payload.get("inpainting_engine"))
        self._select_combo_data(self.source_language, payload.get("source_language"))
        self._select_combo_data(self.target_language, payload.get("target_language"))
        rendering = payload.get("render_settings", {})
        if isinstance(rendering, dict):
            if "font_path" in rendering:
                self.font_path.setText(str(rendering["font_path"] or ""))
            if "min_font_size" in rendering:
                self.min_font_size.setValue(int(rendering["min_font_size"]))
            if "max_font_size" in rendering:
                self.max_font_size.setValue(int(rendering["max_font_size"]))

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def set_progress(self, stage: str, value: int) -> None:
        self.stage_label.setText(f"当前阶段：{stage}")
        self.progress_bar.setValue(value)

    def set_original_preview(self, path: Path) -> None:
        self._set_preview(self.original_preview, path, "无法显示原图")

    def set_result_preview(self, path: Path) -> None:
        self._set_preview(self.result_preview, path, "无法显示译图")

    @staticmethod
    def _set_preview(label: QLabel, path: Path, fallback: str) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            label.setText(fallback)
            return
        label.setPixmap(pixmap)

    def set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.pause_button.setEnabled(busy)
        self.resume_button.setEnabled(busy)
        self.cancel_button.setEnabled(busy)
        for button in self._task_action_buttons:
            button.setEnabled(not busy)
        self.start_button.setText("处理中…" if busy else "开始翻译")

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "处理失败", message)

    def api_values(self) -> dict[str, object]:
        return {
            "id": self.api_channel_id.text().strip(),
            "service_name": self.api_service_name.text().strip(),
            "base_url": self.api_base_url.text().strip(),
            "model": self.api_model.currentText().strip(),
            "api_key": self.api_key.text().strip(),
            "timeout_seconds": self.api_timeout.value(),
            "retries": self.api_retries.value(),
            "enabled": self.api_enabled.isChecked(),
            "max_concurrency": self.api_max_concurrency.value(),
            "rpm_limit": self.api_rpm_limit.value(),
            "priority": self.api_priority.value(),
            "strategy": self.api_strategy.currentData(),
        }

    def set_api_test_busy(self, busy: bool) -> None:
        self.api_test_button.setEnabled(not busy)
        self.api_test_button.setText("测试中…" if busy else "测试连通性")

    def set_api_models_busy(self, busy: bool) -> None:
        self.api_models_button.setEnabled(not busy)
        self.api_models_button.setText("获取中…" if busy else "获取可用模型")

    def set_api_model_choices(self, models: list[str]) -> None:
        current = self.api_model.currentText().strip()
        self.api_model.clear()
        self.api_model.addItems(models)
        if current in models:
            self.api_model.setCurrentText(current)
        elif models:
            self.api_model.setCurrentIndex(0)
        else:
            self.api_model.setEditText(current)

    def set_api_channels(self, channels) -> None:
        self._api_channels = list(channels)
        self.api_table.setRowCount(len(self._api_channels))
        for row, channel in enumerate(self._api_channels):
            values = [
                channel.id, channel.service_name, channel.model, str(channel.priority),
                channel.strategy, "是" if channel.verified else "否", channel.last_error or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.api_table.setItem(row, column, item)

    def _load_api_table_row(self, row: int, _column: int) -> None:
        if not hasattr(self, "_api_channels") or not 0 <= row < len(self._api_channels):
            return
        channel = self._api_channels[row]
        self.api_channel_id.setText(channel.id)
        self.api_service_name.setText(channel.service_name)
        self.api_base_url.setText(channel.base_url)
        self.api_model.setCurrentText(channel.model)
        self.api_timeout.setValue(channel.timeout_seconds)
        self.api_retries.setValue(channel.retries)
        self.api_enabled.setChecked(channel.enabled)
        self.api_max_concurrency.setValue(channel.max_concurrency)
        self.api_rpm_limit.setValue(channel.rpm_limit)
        self.api_priority.setValue(channel.priority)
        self.api_strategy.setCurrentIndex(max(0, self.api_strategy.findData(channel.strategy)))
