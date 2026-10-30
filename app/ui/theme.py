"""Shared visual language for the native desktop workspace."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

COLORS = {"background": "#f6f7f9", "surface": "#ffffff", "text": "#202329",
          "muted": "#626a77", "border": "#e3e6eb", "accent": "#d65a38", "active": "#fff0ea"}

# Original, consistent 24px line artwork; no runtime assets or network access.
_PATHS = {
    "book": '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z"/><path d="M12 5v15"/>',
    "page": '<path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v5h4M9 12h6M9 16h6"/>',
    "edit": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="m8 15 1-4 8-8 3 3-8 8Z"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "scan": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M6 12h12"/>',
    "erase": '<path d="m14 3 7 7-11 11H6l-4-4Z"/><path d="m8 11 7 7M10 21h12"/>',
    "type": '<path d="M4 7V4h16v3M12 4v16M8 20h8"/>',
    "link": '<path d="m9 15 6-6M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 0 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
    "replace": '<path d="M4 7h15l-4-4m4 4-4 4M20 17H5l4 4m-4-4 4-4"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="white"/><circle cx="16" cy="12" r="2" fill="white"/><circle cx="10" cy="18" r="2" fill="white"/>',
    "log": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "chevron": '<path d="m8 10 4 4 4-4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
    "monitor": '<rect x="3" y="3" width="18" height="13" rx="2"/><path d="M12 16v5M8 21h8"/>',
    "retry": '<path d="M4 10a8 8 0 1 1 0 6M4 4v6h6"/>',
    "fit": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5"/>',
    "undo": '<path d="m8 5-5 5 5 5M3 10h11a6 6 0 0 1 0 12"/>',
    "redo": '<path d="m16 5 5 5-5 5m5-5H10a6 6 0 0 0 0 12"/>',
}


def icon(name: str, color: str = "#626a77", size: int = 20) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{_PATHS.get(name, _PATHS["page"])}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result


def panel() -> QFrame:
    frame = QFrame()
    frame.setObjectName("surfacePanel")
    return frame


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def heading(text: str, description: str = "") -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    title = QLabel(text)
    title.setObjectName("pageTitle")
    layout.addWidget(title)
    if description:
        subtitle = QLabel(description)
        subtitle.setObjectName("subTitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
    return widget


def button(text: str, kind: str = "secondary", icon_name: str | None = None) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(f"{kind}Button")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        widget.setIcon(icon(icon_name, "#ffffff" if kind == "primary" else "#626a77"))
        widget.setIconSize(QSize(17, 17))
    return widget


def stylesheet() -> str:
    return """
    QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'; font-size: 14px; color: #202329; }
    QMainWindow, QWidget#appShell, QWidget#contentArea { background: #f6f7f9; }
    QWidget#sidebar { background: #f0f2f5; border-right: 1px solid #e3e6eb; }
    QLabel { background: transparent; border: none; }
    QLabel#brandTitle { font-size: 19px; font-weight: 700; color: #202329; }
    QLabel#brandSubtitle, QLabel#navGroup { font-size: 12px; color: #626a77; }
    QLabel#navGroup { padding: 16px 12px 6px; }
    QLabel#pageTitle { font-size: 26px; font-weight: 700; color: #202329; }
    QLabel#sectionTitle { font-size: 16px; font-weight: 600; }
    QLabel#subTitle, QLabel#hintText, QLabel#statusText { color: #626a77; }
    QLabel#hintText { font-size: 12px; }
    QLabel#fieldLabel { font-size: 13px; font-weight: 600; }
    QFrame#surfacePanel, QWidget#editorInspector { background: #ffffff; border: 1px solid #e3e6eb; border-radius: 8px; }
    QFrame#separator { background: #e3e6eb; border: none; min-height: 1px; max-height: 1px; }
    QTabWidget#pages::pane { background: transparent; border: none; }
    QScrollArea { border: none; background: transparent; }
    QScrollArea > QWidget > QWidget { background: transparent; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
        border: 1px solid #d8dde5; border-radius: 6px; background: #ffffff;
        selection-background-color: #fbd9cc; selection-color: #202329;
    }
    QLineEdit { padding: 7px 9px; min-height: 20px; }
    QPlainTextEdit, QTextEdit { padding: 10px; }
    QComboBox { padding: 7px 30px 7px 9px; min-height: 20px; }
    QComboBox::drop-down { border: none; width: 26px; }
    QComboBox::down-arrow { image: url("DOWN_ICON"); width: 12px; height: 12px; }
    QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #fff0ea; selection-color: #a83b22; padding: 4px; outline: none; }
    QSpinBox, QDoubleSpinBox { padding: 5px 24px 5px 9px; min-height: 24px; }
    QSpinBox QLineEdit, QDoubleSpinBox QLineEdit, QComboBox QLineEdit { padding: 0; border: none; min-height: 0; background: transparent; }
    QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; height: 18px; border: none; border-left: 1px solid #e3e6eb; border-bottom: 1px solid #e3e6eb; border-top-right-radius: 6px; background: #fafbfc; }
    QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; height: 18px; border: none; border-left: 1px solid #e3e6eb; border-bottom-right-radius: 6px; background: #fafbfc; }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("UP_ICON"); width: 12px; height: 12px; }
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("DOWN_ICON"); width: 12px; height: 12px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid #d65a38; }
    QPushButton { background: #ffffff; border: 1px solid #d8dde5; border-radius: 6px; padding: 7px 12px; min-height: 20px; }
    QPushButton:hover { background: #f4f5f7; border-color: #b8c0cc; }
    QPushButton:pressed { background: #e9edf2; }
    QPushButton#compactButton { padding: 7px 8px; }
    QPushButton:focus { border: 1px solid #d65a38; }
    QPushButton:disabled { color: #929aa7; background: #f7f8fa; border-color: #e3e6eb; }
    QPushButton#primaryButton { color: #ffffff; background: #c34b2b; border-color: #c34b2b; font-weight: 600; }
    QPushButton#primaryButton:hover { background: #bc4729; border-color: #bc4729; }
    QPushButton#primaryButton:disabled { color: #f8ede9; background: #cfa797; border-color: #cfa797; }
    QPushButton#dangerButton { color: #b43d34; border-color: transparent; background: transparent; }
    QPushButton#dangerButton:hover { background: #fff0ef; border-color: #f0cbc7; }
    QPushButton#toolButton { border-color: transparent; background: transparent; padding: 6px 9px; }
    QPushButton#toolButton:checked, QPushButton:checked { color: #ad3f24; background: #fff0ea; border-color: #f0c7b8; }
    QPushButton#navButton { text-align: left; border: 1px solid transparent; background: transparent; padding: 9px 12px; font-size: 14px; }
    QPushButton#navButton:hover { background: #e8ebef; }
    QPushButton#navButton:checked { color: #ae3f23; background: #fff0ea; border-color: transparent; font-weight: 600; }
    QPushButton#navButton:focus { border-color: #d65a38; }
    QCheckBox { spacing: 9px; min-height: 24px; }
    QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #aeb7c5; border-radius: 4px; background: #ffffff; }
    QCheckBox::indicator:checked { background: #d65a38; border-color: #d65a38; image: url("CHECK_ICON"); }
    QCheckBox::indicator:disabled { background: #e6e9ee; border-color: #d8dde5; }
    QTableWidget { background: #ffffff; border: 1px solid #e3e6eb; border-radius: 6px; gridline-color: #f0f2f5; selection-background-color: #fff0ea; selection-color: #202329; outline: 0; }
    QTableWidget::item { padding: 8px; border-bottom: 1px solid #f0f2f5; }
    QHeaderView::section { background: #f6f7f9; color: #626a77; padding: 9px 10px; border: none; border-bottom: 1px solid #e3e6eb; font-weight: 500; }
    QTableCornerButton::section { background: #f6f7f9; border: none; }
    QProgressBar { border: none; background: #e6eaf0; border-radius: 4px; min-height: 8px; max-height: 8px; text-align: center; }
    QProgressBar::chunk { background: #d65a38; border-radius: 4px; }
    QSplitter::handle { background: transparent; }
    QSplitter::handle:hover { background: #e3e6eb; }
    QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
    QScrollBar::handle:vertical { background: #c6cdd6; border-radius: 3px; min-height: 28px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
    QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
    QScrollBar::handle:horizontal { background: #c6cdd6; border-radius: 3px; min-width: 28px; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QMenu { background: #ffffff; border: 1px solid #e3e6eb; border-radius: 6px; padding: 5px; }
    QMenu::item { padding: 9px 24px; border-radius: 4px; }
    QMenu::item:selected { background: #fff0ea; color: #ad3f24; }
    QToolTip { color: #ffffff; background: #303641; border: none; padding: 6px 9px; }
    QGraphicsView { background: #eceff3; border: 1px solid #e3e6eb; border-radius: 8px; }
    """.replace("CHECK_ICON", (Path(__file__).parent / "assets/check.svg").as_posix()).replace(
        "UP_ICON", (Path(__file__).parent / "assets/chevron-up.svg").as_posix()).replace(
        "DOWN_ICON", (Path(__file__).parent / "assets/chevron-down.svg").as_posix())
