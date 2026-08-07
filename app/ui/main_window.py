from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.paths import AppPaths
from app.ui.page_editor import PageEditorWidget


class MainWindow(QMainWindow):
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
    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()
    exit_requested = Signal()

    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self._selected_sources: list[Path] = []
        self.setWindowTitle("个人漫画翻译器")
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
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
        self._apply_style()
        self.set_busy(False)
        self._allow_quit = False
        self._tray_hint_shown = False
        self._setup_tray()

    def _setup_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self._app_icon(), self)
        menu = QMenu()
        show_action = menu.addAction("显示主窗口")
        show_action.triggered.connect(self.show_from_tray)
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.request_quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        self._allow_quit = True
        self.tray_icon.hide()
        self.exit_requested.emit()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_quit or not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray_icon.show()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray_icon.showMessage(
                "个人漫画翻译器",
                "窗口已隐藏，任务仍在后台运行；点击托盘图标可恢复，右键可退出。",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    @staticmethod
    def _app_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1570ef"))
        painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
        font = QFont()
        font.setPixelSize(40)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "翻")
        painter.end()
        return QIcon(pixmap)

    def _build_translation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("漫画翻译")
        title.setObjectName("pageTitle")
        subtitle = QLabel("本地检测与OCR，按需调用AI文本翻译；原图不会发送给纯文本模型。")
        subtitle.setObjectName("subTitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        input_grid = QGridLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("支持多图、文件夹、ZIP和EPUB")
        source_button = QPushButton("选择文件…")
        source_button.clicked.connect(self._choose_source)
        folder_button = QPushButton("选择文件夹…")
        folder_button.clicked.connect(self._choose_folder)
        self.batch_name_edit = QLineEdit()
        self.batch_name_edit.setPlaceholderText("多图、文件夹、ZIP、EPUB任务必填；例如：第一卷")
        self.output_edit = QLineEdit(str(self.paths.output_root))
        output_button = QPushButton("选择输出目录…")
        output_button.clicked.connect(self._choose_output)
        input_grid.addWidget(QLabel("输入图片"), 0, 0)
        input_grid.addWidget(self.source_edit, 0, 1)
        input_grid.addWidget(source_button, 0, 2)
        input_grid.addWidget(folder_button, 0, 3)
        input_grid.addWidget(QLabel("批次名称"), 1, 0)
        input_grid.addWidget(self.batch_name_edit, 1, 1, 1, 3)
        input_grid.addWidget(QLabel("输出目录"), 2, 0)
        input_grid.addWidget(self.output_edit, 2, 1)
        input_grid.addWidget(output_button, 2, 2)
        layout.addLayout(input_grid)

        language_row = QHBoxLayout()
        self.source_language = QComboBox()
        self.source_language.addItem("自动检测", "auto")
        self.source_language.addItem("日语", "ja")
        self.source_language.addItem("英语", "en")
        self.source_language.addItem("简体中文", "zh-Hans")
        self.source_language.addItem("繁体中文", "zh-Hant")
        self.target_language = QComboBox()
        self.target_language.addItem("简体中文", "zh-Hans")
        self.target_language.addItem("繁体中文", "zh-Hant")
        self.target_language.addItem("英语", "en")
        self.translate_checkbox = QCheckBox("调用AI翻译")
        self.translate_checkbox.setChecked(True)
        language_row.addWidget(QLabel("源语言"))
        language_row.addWidget(self.source_language)
        language_row.addWidget(QLabel("目标语言"))
        language_row.addWidget(self.target_language)
        language_row.addWidget(self.translate_checkbox)
        language_row.addStretch(1)
        layout.addLayout(language_row)

        speed_row = QHBoxLayout()
        self.concurrent_pages = QSpinBox()
        self.concurrent_pages.setRange(1, 4)
        self.concurrent_pages.setValue(1)
        speed_row.addWidget(QLabel("并行页数"))
        speed_row.addWidget(self.concurrent_pages)
        speed_row.addWidget(QLabel("（>1 时翻译可重叠，OCR仍串行）"))
        speed_row.addStretch(1)
        layout.addLayout(speed_row)

        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["页码", "文件", "状态", "错误"])
        self.queue_table.setMaximumHeight(150)
        layout.addWidget(self.queue_table)
        exchange_row = QHBoxLayout()
        for label, signal in (
            ("重试失败页", self.retry_requested),
            ("导出TXT", self.export_txt_requested),
            ("导出JSON", self.export_json_requested),
            ("导入TXT/JSON并重绘", self.import_text_requested),
            ("导出译图ZIP", self.export_zip_requested),
        ):
            button = QPushButton(label)
            button.clicked.connect(signal.emit)
            exchange_row.addWidget(button)
        exchange_row.addStretch(1)
        layout.addLayout(exchange_row)

        preview_grid = QGridLayout()
        self.original_preview = self._preview_label("原图预览")
        self.result_preview = self._preview_label("译图预览")
        preview_grid.addWidget(self.original_preview, 0, 0)
        preview_grid.addWidget(self.result_preview, 0, 1)
        preview_grid.setColumnStretch(0, 1)
        preview_grid.setColumnStretch(1, 1)
        layout.addLayout(preview_grid, 1)

        progress_row = QHBoxLayout()
        self.stage_label = QLabel("当前阶段：等待开始")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_button = QPushButton("开始翻译")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("继续")
        self.cancel_button = QPushButton("取消")
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.resume_button.clicked.connect(self.resume_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.page_counter = QLabel("第 0 / 0 页")
        progress_row.addWidget(self.stage_label)
        progress_row.addWidget(self.page_counter)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.pause_button)
        progress_row.addWidget(self.resume_button)
        progress_row.addWidget(self.cancel_button)
        progress_row.addWidget(self.start_button)
        layout.addLayout(progress_row)
        return page

    def _build_api_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("OpenAI兼容API")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.api_channel_id = QLineEdit("deepseek-default")
        self.api_service_name = QLineEdit("DeepSeek")
        self.api_base_url = QLineEdit("https://api.deepseek.com/v1")
        self.api_model = QComboBox()
        self.api_model.setEditable(True)
        self.api_model.addItem("deepseek-v4-flash")
        self.api_models_button = QPushButton("获取可用模型")
        self.api_models_button.clicked.connect(self.api_models_requested.emit)
        model_field = QWidget()
        model_layout = QHBoxLayout(model_field)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.api_model, 1)
        model_layout.addWidget(self.api_models_button)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("密钥仅保存到Windows凭据管理器")
        self.api_timeout = QSpinBox()
        self.api_timeout.setRange(5, 600)
        self.api_timeout.setValue(60)
        self.api_retries = QSpinBox()
        self.api_retries.setRange(0, 10)
        self.api_retries.setValue(2)
        self.api_enabled = QCheckBox("启用此通道")
        self.api_enabled.setChecked(True)
        self.api_max_concurrency = QSpinBox()
        self.api_max_concurrency.setRange(1, 32)
        self.api_max_concurrency.setValue(1)
        self.api_rpm_limit = QSpinBox()
        self.api_rpm_limit.setRange(1, 10000)
        self.api_rpm_limit.setValue(60)
        self.api_priority = QSpinBox()
        self.api_priority.setRange(0, 10000)
        self.api_priority.setValue(10)
        self.api_strategy = QComboBox()
        self.api_strategy.addItem("按优先级", "priority")
        self.api_strategy.addItem("同优先级轮询", "round_robin")
        form.addRow("通道编号", self.api_channel_id)
        form.addRow("服务名称", self.api_service_name)
        form.addRow("Base URL", self.api_base_url)
        form.addRow("模型", model_field)
        form.addRow("API Key", self.api_key)
        form.addRow("超时（秒）", self.api_timeout)
        form.addRow("重试次数", self.api_retries)
        form.addRow("启用", self.api_enabled)
        form.addRow("最大并发", self.api_max_concurrency)
        form.addRow("每分钟请求限制", self.api_rpm_limit)
        form.addRow("优先级（小值优先）", self.api_priority)
        form.addRow("策略", self.api_strategy)
        layout.addLayout(form)
        actions = QHBoxLayout()
        save_button = QPushButton("安全保存")
        self.api_test_button = QPushButton("测试连通性")
        revoke_button = QPushButton("撤销本地密钥")
        delete_button = QPushButton("删除通道")
        save_button.clicked.connect(self.api_save_requested.emit)
        self.api_test_button.clicked.connect(self.api_test_requested.emit)
        revoke_button.clicked.connect(self.api_revoke_requested.emit)
        delete_button.clicked.connect(self.api_delete_requested.emit)
        actions.addWidget(save_button)
        actions.addWidget(self.api_test_button)
        actions.addWidget(revoke_button)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.api_status = QLabel("状态：尚未测试")
        layout.addWidget(self.api_status)
        self.api_table = QTableWidget(0, 7)
        self.api_table.setHorizontalHeaderLabels(["编号", "服务", "模型", "优先级", "策略", "已验证", "错误"])
        self.api_table.cellClicked.connect(self._load_api_table_row)
        layout.addWidget(self.api_table)
        return page

    def _build_ocr_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("本地OCR")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.ocr_engine = QComboBox()
        self.ocr_engine.addItem("RapidOCR（ONNX，本地）", "rapidocr")
        self.ocr_engine.addItem("MangaOCR（日漫专用，约400MB，需手动安装）", "mangaocr")
        self.review_threshold = QSpinBox()
        self.review_threshold.setRange(0, 100)
        self.review_threshold.setValue(60)
        self.high_accuracy_ocr = QCheckBox("高精度OCR（更慢但更准，适合高清页）")
        form = QFormLayout()
        form.addRow("OCR引擎", self.ocr_engine)
        form.addRow("识别模式", self.high_accuracy_ocr)
        form.addRow("低置信度阈值（%）", self.review_threshold)
        layout.addLayout(form)
        note = QLabel("OCR组件与模型按需安装。未确认前，程序不会自动下载模型。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_detection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("检测与擦字")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.inpainting_engine = QComboBox()
        self.inpainting_engine.addItem("OpenCV（内置，快速）", "opencv")
        self.inpainting_engine.addItem("LaMa ONNX（可选，本地模型失败时自动降级）", "lama_onnx")
        form = QFormLayout()
        form.addRow("擦字引擎", self.inpainting_engine)
        layout.addLayout(form)
        note = QLabel("LaMa模型必须手动放到 models\\lama.onnx；程序不会自动联网下载。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_library_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.glossary_editor = QPlainTextEdit()
        self.glossary_editor.setPlaceholderText("术语表：每行填写 原文<Tab>译文")
        self.prompt_editor = QPlainTextEdit()
        self.prompt_editor.setPlaceholderText("自定义系统提示词和翻译提示词")
        save_button = QPushButton("保存术语表与提示词")
        save_button.clicked.connect(self.library_save_requested.emit)
        layout.addWidget(QLabel("术语表"))
        layout.addWidget(self.glossary_editor)
        layout.addWidget(QLabel("提示词"))
        layout.addWidget(self.prompt_editor)
        layout.addWidget(save_button)
        return page

    def _build_replacement_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.replacement_editor = QPlainTextEdit()
        self.replacement_editor.setPlaceholderText("每行填写 查找<Tab>替换，可用于过滤词和标点统一")
        layout.addWidget(self.replacement_editor)
        save_button = QPushButton("保存替换规则")
        save_button.clicked.connect(self.replacement_save_requested.emit)
        layout.addWidget(save_button)
        return page

    def _build_preset_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("预设名称，例如：日漫转简中")
        self.preset_list = QComboBox()
        layout.addWidget(self.preset_name)
        layout.addWidget(self.preset_list)
        save_button = QPushButton("保存当前设置为预设")
        save_button.clicked.connect(self.preset_save_requested.emit)
        apply_button = QPushButton("应用选中预设")
        apply_button.clicked.connect(self.preset_apply_requested.emit)
        actions = QHBoxLayout()
        actions.addWidget(save_button)
        actions.addWidget(apply_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(QLabel("预设保存OCR、API、擦字、渲染与输出设置。"))
        layout.addStretch(1)
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["任务", "来源", "状态", "创建时间", "输出目录"])
        layout.addWidget(self.history_table)
        resume_button = QPushButton("恢复选中任务")
        resume_button.clicked.connect(self.history_resume_requested.emit)
        layout.addWidget(resume_button)
        return page

    def _build_render_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("渲染与字体")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.font_path = QLineEdit()
        self.font_path.setPlaceholderText("留空时优先使用项目字体，其次使用Windows中文字体")
        self.min_font_size = QSpinBox()
        self.min_font_size.setRange(6, 72)
        self.min_font_size.setValue(10)
        self.max_font_size = QSpinBox()
        self.max_font_size.setRange(10, 200)
        self.max_font_size.setValue(64)
        form = QFormLayout()
        form.addRow("字体文件", self.font_path)
        form.addRow("最小字号", self.min_font_size)
        form.addRow("最大字号", self.max_font_size)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("运行阶段、页面错误和诊断信息会显示在这里。日志会自动隐藏API Key。")
        layout.addWidget(self.log_view)
        return page

    @staticmethod
    def _preview_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(320, 300)
        label.setFrameShape(QFrame.Shape.StyledPanel)
        label.setStyleSheet("background:#f6f7f9;color:#667085;border:1px solid #d0d5dd;border-radius:8px;")
        return label

    def _choose_source(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择漫画图片、ZIP或EPUB",
            str(self.paths.project_root),
            "漫画输入 (*.png *.jpg *.jpeg *.webp *.bmp *.zip *.epub)",
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
            self.queue_table.setItem(row, 1, QTableWidgetItem(path.name))
            self.queue_table.setItem(row, 2, QTableWidgetItem("等待"))
            self.queue_table.setItem(row, 3, QTableWidgetItem(""))

    def update_queue_page(self, index: int, status: str, error: str = "") -> None:
        if 0 <= index < self.queue_table.rowCount():
            self.queue_table.setItem(index, 2, QTableWidgetItem(status))
            self.queue_table.setItem(index, 3, QTableWidgetItem(error))
            self.page_counter.setText(f"第 {index + 1} / {self.queue_table.rowCount()} 页")

    def set_history(self, tasks) -> None:
        tasks = list(tasks)
        self._history_tasks = tasks
        self.history_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [task.id[:10], task.source_path, task.status.value, task.created_at, task.output_dir]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(value))

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
        label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.pause_button.setEnabled(busy)
        self.resume_button.setEnabled(busy)
        self.cancel_button.setEnabled(busy)
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
                self.api_table.setItem(row, column, QTableWidgetItem(value))

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

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { font-family: 'Microsoft YaHei UI'; font-size: 14px; color: #182230; }
            QMainWindow, QTabWidget::pane { background: #ffffff; }
            QTabBar::tab { padding: 11px 20px; background: #f2f4f7; }
            QTabBar::tab:selected { background: #ffffff; color: #175cd3; border-bottom: 3px solid #2e90fa; }
            QLabel#pageTitle { font-size: 24px; font-weight: 700; margin-top: 8px; }
            QLabel#subTitle { color: #667085; margin-bottom: 12px; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { padding: 7px; border: 1px solid #d0d5dd; border-radius: 6px; background: white; }
            QPushButton { padding: 8px 15px; border: 1px solid #b2ccff; border-radius: 6px; background: #eff4ff; }
            QPushButton:hover { background: #d1e0ff; }
            QPushButton#primaryButton { color: white; background: #1570ef; border: none; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #175cd3; }
            QProgressBar { border: 1px solid #d0d5dd; border-radius: 6px; text-align: center; min-height: 24px; }
            QProgressBar::chunk { background: #2e90fa; border-radius: 5px; }
            """
        )
