from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
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
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.paths import AppPaths


class MainWindow(QMainWindow):
    start_requested = Signal()
    api_save_requested = Signal()
    api_test_requested = Signal()
    api_revoke_requested = Signal()

    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.setWindowTitle("个人漫画翻译器")
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_translation_tab(), "开始翻译")
        self.tabs.addTab(self._build_api_tab(), "API管理")
        self.tabs.addTab(self._build_ocr_tab(), "OCR设置")
        self.tabs.addTab(self._build_render_tab(), "渲染与字体")
        self.tabs.addTab(self._build_log_tab(), "日志与诊断")
        self._apply_style()

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
        self.source_edit.setPlaceholderText("请选择PNG、JPG、WEBP或BMP图片")
        source_button = QPushButton("选择图片…")
        source_button.clicked.connect(self._choose_source)
        self.output_edit = QLineEdit(str(self.paths.output_root))
        output_button = QPushButton("选择输出目录…")
        output_button.clicked.connect(self._choose_output)
        input_grid.addWidget(QLabel("输入图片"), 0, 0)
        input_grid.addWidget(self.source_edit, 0, 1)
        input_grid.addWidget(source_button, 0, 2)
        input_grid.addWidget(QLabel("输出目录"), 1, 0)
        input_grid.addWidget(self.output_edit, 1, 1)
        input_grid.addWidget(output_button, 1, 2)
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
        progress_row.addWidget(self.stage_label)
        progress_row.addWidget(self.progress_bar, 1)
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
        self.api_service_name = QLineEdit("DeepSeek")
        self.api_base_url = QLineEdit("https://api.deepseek.com/v1")
        self.api_model = QLineEdit("deepseek-chat")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("密钥仅保存到Windows凭据管理器")
        self.api_timeout = QSpinBox()
        self.api_timeout.setRange(5, 600)
        self.api_timeout.setValue(60)
        self.api_retries = QSpinBox()
        self.api_retries.setRange(0, 10)
        self.api_retries.setValue(2)
        form.addRow("服务名称", self.api_service_name)
        form.addRow("Base URL", self.api_base_url)
        form.addRow("模型", self.api_model)
        form.addRow("API Key", self.api_key)
        form.addRow("超时（秒）", self.api_timeout)
        form.addRow("重试次数", self.api_retries)
        layout.addLayout(form)
        actions = QHBoxLayout()
        save_button = QPushButton("安全保存")
        test_button = QPushButton("测试连通性")
        revoke_button = QPushButton("撤销本地密钥")
        save_button.clicked.connect(self.api_save_requested.emit)
        test_button.clicked.connect(self.api_test_requested.emit)
        revoke_button.clicked.connect(self.api_revoke_requested.emit)
        actions.addWidget(save_button)
        actions.addWidget(test_button)
        actions.addWidget(revoke_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.api_status = QLabel("状态：尚未测试")
        layout.addWidget(self.api_status)
        layout.addStretch(1)
        return page

    def _build_ocr_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("本地OCR")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.ocr_engine = QComboBox()
        self.ocr_engine.addItem("RapidOCR（ONNX，本地）", "rapidocr")
        self.review_threshold = QSpinBox()
        self.review_threshold.setRange(0, 100)
        self.review_threshold.setValue(60)
        form = QFormLayout()
        form.addRow("OCR引擎", self.ocr_engine)
        form.addRow("低置信度阈值（%）", self.review_threshold)
        layout.addLayout(form)
        note = QLabel("OCR组件与模型按需安装。未确认前，程序不会自动下载模型。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
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
        path, _ = QFileDialog.getOpenFileName(self, "选择漫画图片", str(self.paths.project_root), "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.source_edit.setText(path)
            self.set_original_preview(Path(path))

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
        self.start_button.setText("处理中…" if busy else "开始翻译")

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "处理失败", message)

    def api_values(self) -> dict[str, object]:
        return {
            "service_name": self.api_service_name.text().strip(),
            "base_url": self.api_base_url.text().strip(),
            "model": self.api_model.text().strip(),
            "api_key": self.api_key.text().strip(),
            "timeout_seconds": self.api_timeout.value(),
            "retries": self.api_retries.value(),
        }

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
