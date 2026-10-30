from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QTableWidget


class PreviewLabel(QLabel):
    """Keep the original pixmap and fit it again when its pane is resized."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self._source_pixmap = QPixmap()
        self.title = title
        self.description = description
        self.setMinimumSize(100, 120)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("previewCanvas")
        self.setStyleSheet("background:#f6f7f9;border:none;border-radius:6px;")
        self.setAccessibleName(title)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self._fit()

    def setText(self, text: str) -> None:
        self._source_pixmap = QPixmap()
        super().setText(text)

    def _fit(self) -> None:
        if not self._source_pixmap.isNull():
            super().setPixmap(self._source_pixmap.scaled(
                max(1, self.width() - 20), max(1, self.height() - 20),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._source_pixmap.isNull() or self.text():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2 - 28
        pen = QPen(QColor("#bdc5d0"), 1.5)
        painter.setPen(pen)
        painter.setBrush(QColor("#ffffff"))
        page = QRectF(cx - 28, cy - 38, 56, 72)
        painter.drawRoundedRect(page, 5, 5)
        painter.drawLine(QPointF(cx - 17, cy - 20), QPointF(cx + 17, cy - 20))
        painter.drawRect(QRectF(cx - 17, cy - 10, 34, 17))
        painter.drawLine(QPointF(cx - 17, cy + 18), QPointF(cx + 12, cy + 18))
        painter.setPen(QColor("#626a77"))
        font = self.font()
        font.setPixelSize(13)
        painter.setFont(font)
        painter.drawText(QRectF(12, cy + 52, self.width() - 24, 55),
                         Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, self.description)
        painter.end()


class EmptyTable(QTableWidget):
    def __init__(self, columns: int, message: str) -> None:
        super().__init__(0, columns)
        self.empty_message = message

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#626a77"))
            painter.drawText(self.viewport().rect().adjusted(16, 8, -16, -8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_message)
            painter.end()
