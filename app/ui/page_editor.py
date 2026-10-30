from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict
from pathlib import Path

import numpy as np

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QMouseEvent, QPainter, QPen, QPixmap, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QColorDialog,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.domain.models import TextRegion
from app.services.page_artifacts import manual_mask
from app.ui.theme import icon, section_title


class _EditorTextEdit(QPlainTextEdit):
    """A readable multiline field preserving the editor's text/setText API."""

    def __init__(self, placeholder: str, height: int) -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(height)
        self.setTabChangesFocus(True)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        self.setPlainText(value)


class RegionItem(QGraphicsRectItem):
    def __init__(self, region: TextRegion) -> None:
        left, top, right, bottom = region.bounds
        super().__init__(QRectF(0, 0, max(1, right - left), max(1, bottom - top)))
        self.region_id = region.id
        self.setPos(left, top)
        self.setRotation(region.angle)
        self.setTransformOriginPoint(self.rect().center())
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemIsFocusable
        )
        if region.metadata.get("review_decision") == "rejected":
            self.setPen(QPen(QColor("#667085"), 2, Qt.PenStyle.DashLine))
            self.setBrush(QColor(102, 112, 133, 20))
        elif region.needs_review:
            self.setPen(QPen(QColor("#dc2626"), 3, Qt.PenStyle.DashLine))
            self.setBrush(QColor(220, 38, 38, 34))
        else:
            self.setPen(QPen(QColor("#d65a38"), 2, Qt.PenStyle.DashLine))
            self.setBrush(QColor(214, 90, 56, 20))

    def paint(self, painter, option, widget=None) -> None:
        if not self.isSelected():
            super().paint(painter, option, widget)
            return
        painter.save()
        pen = QPen(QColor("#d65a38"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QColor(214, 90, 56, 20))
        painter.drawRect(self.rect())
        painter.restore()


class MaskGraphicsView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, editor: "PageEditorWidget") -> None:
        super().__init__(scene)
        self.editor = editor
        self._last_mask_point: tuple[int, int] | None = None
        self._drag_before: list[dict] | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.editor.mask_mode is not None:
            self._last_mask_point = None
            self.editor.begin_mask_stroke(erase=self.editor.mask_mode == "erase")
            self._paint(event)
            event.accept()
            return
        if event.button() is Qt.MouseButton.LeftButton and any(
            isinstance(item, RegionItem) for item in self.items(event.position().toPoint())
        ):
            self.editor.apply_controls()
            self._drag_before = self.editor._snapshot()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self.editor.mask_mode is not None:
            self._paint(event)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_mask_point = None
        if event.button() is Qt.MouseButton.LeftButton and self.editor.mask_mode is not None:
            self.editor.end_mask_stroke()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if self._drag_before is not None:
            before, self._drag_before = self._drag_before, None
            self.editor._commit_drag(before)

    def _paint(self, event: QMouseEvent) -> None:
        point = self.mapToScene(event.position().toPoint())
        self.editor.paint_mask(
            round(point.x()), round(point.y()), radius=self.editor.mask_radius.value(),
            erase=self.editor.mask_mode == "erase",
            previous=self._last_mask_point,
        )
        self._last_mask_point = (round(point.x()), round(point.y()))


class _SnapshotCommand(QUndoCommand):
    def __init__(self, editor: "PageEditorWidget", before: list[dict], after: list[dict], label: str) -> None:
        super().__init__(label)
        self.editor = editor
        self.before = before
        self.after = after

    def undo(self) -> None:
        self.editor._restore_snapshot(self.before)

    def redo(self) -> None:
        self.editor._restore_snapshot(self.after)


class _MaskOverlayItem(QGraphicsItem):
    """Draw just the exposed rectangle, without converting the page to a pixmap."""

    def __init__(self, image: QImage) -> None:
        super().__init__()
        self.image = image
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(50)

    def boundingRect(self) -> QRectF:
        return QRectF(self.image.rect())

    def paint(self, painter, option, widget=None) -> None:
        exposed = option.exposedRect.intersected(self.boundingRect()).toAlignedRect()
        if not exposed.isEmpty():
            painter.drawImage(QRectF(exposed), self.image, QRectF(exposed))


class _MaskStrokeCommand(QUndoCommand):
    def __init__(self, editor: "PageEditorWidget", patches: list[tuple[QRect, QImage, QImage]], *, erase: bool) -> None:
        super().__init__("擦除补充蒙版" if erase else "绘制补充蒙版")
        self.editor = editor
        self.patches = patches
        self._first_redo = True

    @property
    def storage_bytes(self) -> int:
        return sum(before.sizeInBytes() + after.sizeInBytes() for _, before, after in self.patches)

    def undo(self) -> None:
        for rectangle, before, _ in self.patches:
            self.editor._restore_mask_patch(rectangle, before)

    def redo(self) -> None:
        if self._first_redo:
            self._first_redo = False
            return
        for rectangle, _, after in self.patches:
            self.editor._restore_mask_patch(rectangle, after)


class PageEditorWidget(QWidget):
    """单页可视化编辑器：移动/缩放/旋转文本框、涂擦与撤销重做。"""

    open_requested = Signal()
    render_requested = Signal()
    next_review_page_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.view = MaskGraphicsView(self.scene, self)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.undo_stack = QUndoStack(self)
        self._background: QGraphicsPixmapItem | None = None
        self._items: dict[str, RegionItem] = {}
        self._regions: dict[str, TextRegion] = {}
        self._document: dict = {}
        self._image_path: Path | None = None
        self._metadata_path: Path | None = None
        self._mask: QImage | None = None
        self._mask_visual: QImage | None = None
        self._mask_item: _MaskOverlayItem | None = None
        self._stroke_before: dict[tuple[int, int], tuple[QRect, QImage]] | None = None
        self._stroke_erasing = False
        self.mask_mode: str | None = None
        self._text_color = QColor("#111111")
        self._updating_controls = False
        self._original_pixmap = QPixmap()
        self._result_pixmap = QPixmap()
        self._auto_fit = True
        self._build_ui()
        self.scene.selectionChanged.connect(self._selection_changed)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save_page)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.duplicate_selected)
        QShortcut(QKeySequence.StandardKey.Delete, self, activated=self.delete_selected)
        QShortcut(QKeySequence("F8"), self, activated=self.next_review_region)

    @property
    def region_count(self) -> int:
        return len(self._regions)

    @property
    def metadata_path(self) -> Path | None:
        return self._metadata_path

    @property
    def pending_review_count(self) -> int:
        return len(self._review_region_ids())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("editorToolbar")
        toolbar.setStyleSheet("QFrame#editorToolbar { background:white; border:1px solid #e3e6eb; border-radius:8px; }")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(10)
        open_button = QPushButton("打开页面JSON…")
        open_button.setIcon(icon("folder"))
        open_button.clicked.connect(self.open_requested.emit)
        tools.addWidget(open_button)
        self.preview_mode = QComboBox()
        self.preview_mode.addItem("原图 · 编辑与对照", "original")
        self.preview_mode.addItem("译图 · 重绘结果", "result")
        self.preview_mode.setToolTip("切换原始画面和重绘后的译图，检查文字与背景效果")
        self.preview_mode.setMinimumWidth(170)
        self.preview_mode.setAccessibleName("预览内容")
        self.preview_mode.currentIndexChanged.connect(self._update_preview)
        tools.addWidget(self.preview_mode)
        fit_button = QPushButton("适应窗口")
        fit_button.setToolTip("把整页缩放到画布范围")
        fit_button.clicked.connect(self.fit_page)
        tools.addWidget(fit_button)
        tools.addStretch(1)
        undo_button = QPushButton("撤销")
        redo_button = QPushButton("重做")
        undo_button.setToolTip("撤销 · Ctrl+Z")
        redo_button.setToolTip("重做 · Ctrl+Shift+Z")
        undo_button.setIcon(icon("undo"))
        redo_button.setIcon(icon("redo"))
        undo_button.clicked.connect(self.undo_stack.undo)
        redo_button.clicked.connect(self.undo_stack.redo)
        undo_button.setEnabled(False)
        redo_button.setEnabled(False)
        self.undo_stack.canUndoChanged.connect(undo_button.setEnabled)
        self.undo_stack.canRedoChanged.connect(redo_button.setEnabled)
        tools.addWidget(undo_button)
        tools.addWidget(redo_button)
        root.addWidget(toolbar)

        body = QHBoxLayout()
        body.setSpacing(16)
        canvas = QFrame()
        canvas.setObjectName("editorCanvas")
        canvas.setStyleSheet("QFrame#editorCanvas { background:#e9edf1; border:1px solid #e3e6eb; border-radius:8px; }")
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        self.view.setObjectName("pageCanvasView")
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet("QGraphicsView#pageCanvasView { background:#e9edf1; border:0; }")
        self.view.setMinimumWidth(180)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        canvas_layout.addWidget(self.view, 1)
        empty_layout = QVBoxLayout(self.view.viewport())
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.addStretch(1)
        self.empty_state = QWidget()
        self.empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty = QVBoxLayout(self.empty_state)
        empty.setSpacing(12)
        symbol = QLabel()
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setPixmap(icon("image", color="#9aa4b2", size=44).pixmap(44, 44))
        empty.addWidget(symbol)
        empty_title = QLabel("从一页漫画开始")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size:18px;font-weight:600;color:#626a77")
        empty.addWidget(empty_title)
        empty_hint = QLabel("打开已处理的页面 JSON，或在翻译工作台点击“编辑选中页”。")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setWordWrap(True)
        empty_hint.setStyleSheet("color:#626a77;line-height:1.6")
        empty.addWidget(empty_hint)
        empty_layout.addWidget(self.empty_state)
        empty_layout.addStretch(1)
        canvas_footer = QHBoxLayout()
        canvas_footer.setContentsMargins(14, 9, 12, 9)
        canvas_footer.setSpacing(8)
        self.page_status = QLabel("选择文字框后，可在右侧修改译文与排版")
        self.page_status.setObjectName("statusText")
        self.page_status.setWordWrap(True)
        canvas_footer.addWidget(self.page_status, 1)
        zoom_out = QPushButton("−")
        zoom_in = QPushButton("+")
        for zoom_button in (zoom_out, zoom_in):
            zoom_button.setObjectName("toolButton")
            zoom_button.setFixedSize(30, 30)
        zoom_out.setToolTip("缩小画布")
        zoom_in.setToolTip("放大画布")
        zoom_out.setAccessibleName("缩小画布")
        zoom_in.setAccessibleName("放大画布")
        zoom_out.clicked.connect(lambda: self.zoom_page(1 / 1.2))
        zoom_in.clicked.connect(lambda: self.zoom_page(1.2))
        self.zoom_label = QLabel("—")
        self.zoom_label.setMinimumWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setObjectName("statusText")
        canvas_footer.addWidget(zoom_out)
        canvas_footer.addWidget(self.zoom_label)
        canvas_footer.addWidget(zoom_in)
        canvas_layout.addLayout(canvas_footer)
        body.addWidget(canvas, 1)

        inspector = QFrame()
        inspector.setObjectName("editorInspector")
        inspector.setMinimumWidth(300)
        inspector.setMaximumWidth(330)
        inspector.setStyleSheet("QFrame#editorInspector { background:white; border:1px solid #e3e6eb; border-radius:8px; }")
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(0)
        review_bar = QFrame()
        review_bar.setObjectName("editorReviewBar")
        review_bar.setStyleSheet("QFrame#editorReviewBar { border:0; border-bottom:1px solid #e3e6eb; background:white; }")
        review_navigation = QHBoxLayout(review_bar)
        review_navigation.setContentsMargins(14, 10, 14, 10)
        review_navigation.setSpacing(8)
        self.review_counter = QLabel("待检查 0 处")
        self.review_counter.setObjectName("statusText")
        self.next_review_button = QPushButton("下一处待检查")
        self.next_review_button.setToolTip("跳至当前页下一处待检查文字框；本页完成后查找其他页面 · F8")
        self.next_review_button.clicked.connect(self.next_review_region)
        review_navigation.addWidget(self.review_counter, 1)
        review_navigation.addWidget(self.next_review_button)
        inspector_layout.addWidget(review_bar)
        panel = QWidget()
        panel.setObjectName("editorInspectorBody")
        panel.setStyleSheet("QWidget#editorInspectorBody { background:white; }")
        side = QVBoxLayout(panel)
        side.setContentsMargins(16, 16, 16, 16)
        side.setSpacing(12)
        side.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        def group(title: str) -> None:
            if side.count():
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("background:#edf0f3;border:0;max-height:1px;")
                side.addWidget(line)
            side.addWidget(section_title(title))

        group("文本内容")
        self.source_text = _EditorTextEdit("识别到的原文会显示在这里", 76)
        self.source_text.setObjectName("editorSourceText")
        self.source_text.setAccessibleName("原文")
        self.translation_text = _EditorTextEdit("选择文字框，输入或修改译文", 92)
        self.translation_text.setObjectName("editorTranslationText")
        self.translation_text.setAccessibleName("译文")
        side.addWidget(QLabel("原文"))
        side.addWidget(self.source_text)
        side.addWidget(QLabel("译文"))
        side.addWidget(self.translation_text)

        group("人工检查")
        self.review_status = QLabel("状态：未选择文字框")
        self.review_status.setWordWrap(True)
        self.review_status.setObjectName("statusText")
        side.addWidget(self.review_status)
        self.render_warning_label = QLabel()
        self.render_warning_label.setObjectName("renderWarning")
        self.render_warning_label.setWordWrap(True)
        self.render_warning_label.setStyleSheet("color:#a83b22;background:#fff0ea;padding:10px;border-radius:6px;")
        self.render_warning_label.hide()
        side.addWidget(self.render_warning_label)
        review_actions = QHBoxLayout()
        review_actions.setSpacing(8)
        self._review_buttons = []
        for label, decision in (("确认处理", "accepted"), ("保留原文", "rejected")):
            button = QPushButton(label)
            button.setObjectName("secondaryButton")
            button.clicked.connect(lambda _checked=False, value=decision: self._commit_controls(value))
            self._review_buttons.append(button)
            review_actions.addWidget(button)
        side.addLayout(review_actions)

        group("文字样式")
        self.font_size = self._geometry_spinbox(0, 300)
        self.font_size.setSpecialValueText("自动")
        self.font_size.setToolTip("自动使用全局字号范围；指定字号会在文本框放不下时缩小。")
        self.outline_width = self._geometry_spinbox(0, 20)
        self.outline_width.setValue(1)
        self.color_button = QPushButton("选择颜色")
        self.color_button.clicked.connect(self._choose_text_color)
        self._update_color_button()
        self.direction = QComboBox()
        self.direction.addItem("横排", "horizontal")
        self.direction.addItem("竖排", "vertical")
        self.alignment = QComboBox()
        self.alignment.addItem("居中", "center")
        self.alignment.addItem("左对齐", "left")
        self.alignment.addItem("右对齐", "right")
        style = QFormLayout()
        style.setHorizontalSpacing(14)
        style.setVerticalSpacing(10)
        style.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        style.addRow("字号", self.font_size)
        style.addRow("方向", self.direction)
        style.addRow("对齐", self.alignment)
        style.addRow("颜色", self.color_button)
        style.addRow("描边", self.outline_width)
        side.addLayout(style)

        group("位置与角度")
        self.x_value = self._geometry_spinbox(-100_000, 100_000)
        self.y_value = self._geometry_spinbox(-100_000, 100_000)
        self.width_value = self._geometry_spinbox(1, 100_000)
        self.height_value = self._geometry_spinbox(1, 100_000)
        self.rotation_value = self._geometry_spinbox(-180, 180)
        geometry = QGridLayout()
        geometry.setHorizontalSpacing(8)
        geometry.setVerticalSpacing(10)
        for row, pairs in enumerate(((("X", self.x_value), ("Y", self.y_value)), (("宽", self.width_value), ("高", self.height_value)))):
            for column, (label, control) in enumerate(pairs):
                geometry.addWidget(QLabel(label), row, column * 2)
                control.setMinimumWidth(62)
                geometry.addWidget(control, row, column * 2 + 1)
        side.addLayout(geometry)
        rotation = QFormLayout()
        rotation.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        rotation.addRow("旋转角度", self.rotation_value)
        side.addLayout(rotation)
        apply_button = QPushButton("应用属性")
        apply_button.clicked.connect(self.apply_controls)
        side.addWidget(apply_button)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._selection_buttons = []
        for label, callback in (("添加", self.add_region), ("复制", self.duplicate_selected), ("删除", self.delete_selected)):
            button = QPushButton(label)
            if label == "删除":
                button.setObjectName("dangerButton")
            button.clicked.connect(callback)
            actions.addWidget(button)
            if label != "添加":
                self._selection_buttons.append(button)
        side.addLayout(actions)

        group("蒙版设置")
        mask_actions = QGridLayout()
        mask_actions.setSpacing(8)
        self._mask_buttons = {}
        for index, (label, mode) in enumerate((("蒙版画笔", "paint"), ("橡皮擦", "erase"), ("结束绘制", None))):
            button = QPushButton(label)
            button.setObjectName("toolButton")
            button.setCheckable(True)
            button.setStyleSheet("QPushButton:checked { color:#bc4729; background:#fff0ea; border-color:#d65a38; }")
            button.clicked.connect(lambda _checked=False, selected_mode=mode: self._set_mask_mode(selected_mode))
            self._mask_buttons[mode] = button
            mask_actions.addWidget(button, index // 2, index % 2)
        side.addLayout(mask_actions)
        self.mask_radius = self._geometry_spinbox(1, 200)
        self.mask_radius.setValue(16)
        mask_size = QFormLayout()
        mask_size.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        mask_size.addRow("画笔半径（像素）", self.mask_radius)
        side.addLayout(mask_size)
        self.mask_overlay = QCheckBox("显示补充擦字蒙版")
        self.mask_overlay.setChecked(True)
        self.mask_overlay.toggled.connect(self._update_overlay_visibility)
        side.addWidget(self.mask_overlay)
        mask_hint = QLabel("画笔补充擦字范围，橡皮擦撤回补充蒙版。移动译文框不会扩大原文擦除范围；保留原文的区域受到保护。")
        mask_hint.setObjectName("subTitle")
        mask_hint.setWordWrap(True)
        side.addWidget(mask_hint)
        toggle_boxes = QPushButton("显示/隐藏检测框")
        toggle_boxes.clicked.connect(self.toggle_region_boxes)
        side.addWidget(toggle_boxes)
        side.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("editorInspectorScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        self._inspector_scroll = scroll
        inspector_layout.addWidget(scroll, 1)

        save_area = QFrame()
        save_area.setObjectName("editorSaveBar")
        save_area.setStyleSheet("QFrame#editorSaveBar { background:white; border:0; border-top:1px solid #e3e6eb; }")
        saves = QHBoxLayout(save_area)
        saves.setContentsMargins(12, 12, 12, 12)
        saves.setSpacing(8)
        save_button = QPushButton("保存编辑状态")
        save_button.setToolTip("保存文字、位置、样式与蒙版 · Ctrl+S")
        save_button.clicked.connect(self.save_page)
        render_button = QPushButton("保存并重绘译图")
        render_button.setObjectName("primaryButton")
        render_button.clicked.connect(self.render_requested.emit)
        saves.addWidget(save_button)
        saves.addWidget(render_button)
        inspector_layout.addWidget(save_area)
        body.addWidget(inspector)
        root.addLayout(body, 1)

        self._selection_controls = [self.source_text, self.translation_text, self.x_value, self.y_value,
            self.width_value, self.height_value, self.rotation_value, self.font_size, self.outline_width,
            self.color_button, self.direction, self.alignment, apply_button, *self._review_buttons,
            *self._selection_buttons]
        self._page_controls = [save_button, render_button, toggle_boxes, self.mask_radius,
                               self.mask_overlay, *self._mask_buttons.values()]
        self._set_selection_available(False)
        for control in self._page_controls:
            control.setEnabled(False)

    def _set_selection_available(self, available: bool) -> None:
        for control in self._selection_controls:
            control.setEnabled(available)

    def _update_color_button(self) -> None:
        swatch = QPixmap(20, 20)
        swatch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(swatch)
        painter.setPen(QPen(QColor("#c9ced6"), 1))
        painter.setBrush(self._text_color)
        painter.drawRoundedRect(2, 2, 16, 16, 3, 3)
        painter.end()
        self.color_button.setIcon(QIcon(swatch))
        self.color_button.setToolTip(f"当前文字颜色：{self._text_color.name()}")

    def fit_page(self) -> None:
        self._auto_fit = True
        if self._background is not None:
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._update_zoom_label()

    def zoom_page(self, factor: float) -> None:
        if self._background is None:
            return
        proposed = self.view.transform().m11() * factor
        if 0 < proposed <= 8 and (factor > 1 or proposed >= 0.05):
            self._auto_fit = False
            self.view.scale(factor, factor)
            self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(f"{round(self.view.transform().m11() * 100)}%")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self.fit_page)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if getattr(self, "_auto_fit", False):
            QTimer.singleShot(0, self.fit_page)

    @staticmethod
    def _geometry_spinbox(minimum: int, maximum: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(minimum, maximum)
        return box

    def load_page(self, image_path: Path, metadata_path: Path, *, original_image: np.ndarray | None = None,
                  original_available: bool = True) -> None:
        image_path, metadata_path = Path(image_path), Path(metadata_path)
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(document.get("regions"), list):
            raise ValueError("页面JSON缺少regions数组")
        if original_image is None:
            pixmap = QPixmap(str(image_path))
        else:
            rgb = original_image[:, :, ::-1].copy()
            pixmap = QPixmap.fromImage(QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy())
        if pixmap.isNull():
            raise ValueError(f"无法加载编辑图片：{image_path}")
        mask = manual_mask(metadata_path, document, (pixmap.height(), pixmap.width()))
        regions = []
        ids = set()
        for data in document["regions"]:
            region = TextRegion(
                id=str(data["id"]), polygon=[(int(x), int(y)) for x, y in data["polygon"]],
                source_text=str(data.get("source_text", "")), translated_text=str(data.get("translated_text", "")),
                confidence=float(data.get("confidence", 0)), direction=str(data.get("direction", "horizontal")),
                angle=float(data.get("angle", 0)), needs_review=bool(data.get("needs_review", False)),
                metadata=dict(data.get("metadata", {})),
            )
            if region.id in ids or len(region.polygon) < 3:
                raise ValueError("页面JSON包含重复文本框编号或无效坐标。")
            ids.add(region.id)
            region.metadata.setdefault("source_polygon", [list(point) for point in region.polygon])
            regions.append(region)
        self._stroke_before = None
        self.view._drag_before = None
        self.scene.clear()
        self._items.clear()
        self._regions.clear()
        self._document = document
        self._image_path = image_path
        self._metadata_path = metadata_path
        self._original_pixmap = pixmap
        result_path = metadata_path.parent.parent / "translated" / f"{metadata_path.stem}_translated.png"
        self._result_pixmap = QPixmap(str(result_path)) if result_path.is_file() else QPixmap()
        self.preview_mode.blockSignals(True)
        self.preview_mode.setCurrentIndex(0)
        self.preview_mode.blockSignals(False)
        self.page_status.setText("已加载原图，可切换前后对照。" if original_available else "原图副本缺失：可编辑现有修复图，但不能恢复被擦除的原文。")
        self._set_mask_mode(None)
        self._background = self.scene.addPixmap(pixmap)
        self._background.setZValue(-100)
        self.scene.setSceneRect(QRectF(pixmap.rect()))
        self._mask = QImage(mask.data, mask.shape[1], mask.shape[0], mask.strides[0], QImage.Format.Format_Grayscale8).copy()
        overlay = np.zeros((pixmap.height(), pixmap.width(), 4), dtype=np.uint8)
        overlay[:, :, :3] = (255, 40, 40)
        overlay[:, :, 3] = np.where(mask > 0, 110, 0)
        self._mask_visual = QImage(overlay.data, pixmap.width(), pixmap.height(), overlay.strides[0], QImage.Format.Format_RGBA8888).copy()
        self._mask_item = _MaskOverlayItem(self._mask_visual)
        self.scene.addItem(self._mask_item)
        self._update_overlay_visibility()
        for region in regions:
            self._add_region_item(region)
        self.empty_state.hide()
        for control in self._page_controls:
            control.setEnabled(True)
        self.undo_stack.clear()
        self._refresh_review_counter()
        self.fit_page()

    def select_region(self, region_id: str) -> None:
        self.scene.clearSelection()
        self._items[region_id].setSelected(True)

    def _review_region_ids(self) -> list[str]:
        return [region.id for region in self._regions.values()
                if region.metadata.get("review_decision") != "rejected"
                and (region.needs_review or str(region.metadata.get("render_warning") or "").strip())]

    def _refresh_review_counter(self) -> None:
        self.review_counter.setText(f"待检查 {self.pending_review_count} 处")

    def next_review_region(self) -> str | None:
        # Keep typed edits when navigating, without accepting a review decision.
        self.apply_controls()
        pending = self._review_region_ids()
        self._refresh_review_counter()
        if not pending:
            self.page_status.setText("当前页已没有待检查项。")
            self.next_review_page_requested.emit()
            return None
        current = self.selected_region_id()
        index = (pending.index(current) + 1) % len(pending) if current in pending else 0
        selected = pending[index]
        self.select_region(selected)
        self.view.ensureVisible(self._items[selected], 24, 24)
        # A newly shown, wrapped warning changes the inspector layout. Scroll
        # after that layout pass so the whole reason is readable on small screens.
        QTimer.singleShot(0, self._ensure_review_visible)
        return selected

    def _ensure_review_visible(self) -> None:
        if self.selected_region_id() is None:
            return
        anchor = self.render_warning_label if not self.render_warning_label.isHidden() else self.review_status
        self._inspector_scroll.ensureWidgetVisible(anchor, 0, 12)

    def show_rendered_preview(self, image_path: Path) -> None:
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise ValueError(f"无法加载重绘图片：{image_path}")
        self._result_pixmap = pixmap
        if self._metadata_path is not None:
            latest = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            self._document["render_settings"] = latest.get("render_settings", {})
            for key in ("inpaint_cache", "original_available"):
                if key in latest:
                    self._document[key] = latest[key]
                else:
                    self._document.pop(key, None)
            for data in latest.get("regions", []):
                region = self._regions.get(str(data.get("id", "")))
                if region is None:
                    continue
                diagnostics = data.get("metadata", {})
                for key in ("render_warning", "rendered_font_size"):
                    if key in diagnostics:
                        region.metadata[key] = diagnostics[key]
                    else:
                        region.metadata.pop(key, None)
            self._refresh_review_counter()
            selected = self.selected_region_id()
            if selected is not None:
                self._show_review_status(self._regions[selected])
        self.preview_mode.setCurrentIndex(1)
        self._update_preview()
        self.page_status.setText("已重绘：切换上方选项可比较原图与译图。")

    def _update_preview(self) -> None:
        result = self.preview_mode.currentData() == "result"
        pixmap = self._result_pixmap if result else self._original_pixmap
        if self._background is not None:
            self._background.setPixmap(pixmap if not pixmap.isNull() else self._original_pixmap)
        self._update_overlay_visibility()

    def _update_overlay_visibility(self) -> None:
        if self._mask_item is not None:
            self._mask_item.setVisible(self.mask_overlay.isChecked() and self.preview_mode.currentData() == "original")

    def selected_region_id(self) -> str | None:
        selected = self.scene.selectedItems()
        return selected[0].region_id if selected and isinstance(selected[0], RegionItem) else None

    def set_selected_translation(self, text: str) -> None:
        region_id = self.selected_region_id()
        if region_id is None:
            return
        self._regions[region_id].translated_text = text
        self.translation_text.setText(text)

    def update_selected_geometry(self, x: int, y: int, width: int, height: int, angle: int) -> None:
        region_id = self.selected_region_id()
        if region_id is None:
            return

        def mutate() -> None:
            item = self._items[region_id]
            item.setPos(x, y)
            item.setRect(0, 0, max(1, width), max(1, height))
            item.setTransformOriginPoint(item.rect().center())
            item.setRotation(angle)
            self._sync_region(region_id)

        self._record_change("调整文本框", mutate)
        self.select_region(region_id)

    def add_region(self) -> None:
        def mutate() -> None:
            region = TextRegion(
                id=uuid.uuid4().hex,
                polygon=[(20, 20), (220, 20), (220, 100), (20, 100)],
                needs_review=True,
                metadata={"source_polygon": []},
            )
            self._add_region_item(region)

        self._record_change("添加文本框", mutate)

    def duplicate_selected(self) -> None:
        region_id = self.selected_region_id()
        if region_id is None:
            return

        def mutate() -> None:
            self._sync_region(region_id)
            data = asdict(self._regions[region_id])
            data["id"] = uuid.uuid4().hex
            data["polygon"] = [(x + 12, y + 12) for x, y in data["polygon"]]
            self._add_region_item(TextRegion(**data))

        self._record_change("复制文本框", mutate)

    def delete_selected(self) -> None:
        region_id = self.selected_region_id()
        if region_id is None:
            return

        def mutate() -> None:
            self.scene.removeItem(self._items.pop(region_id))
            self._regions.pop(region_id)

        self._record_change("删除文本框", mutate)

    def apply_controls(self) -> None:
        self._commit_controls()

    def _commit_controls(self, decision: str | None = None) -> None:
        if self._updating_controls:
            return
        region_id = self.selected_region_id()
        if region_id is None:
            return

        def mutate() -> None:
            region = self._regions[region_id]
            region.source_text = self.source_text.text()
            region.translated_text = self.translation_text.text()
            region.direction = str(self.direction.currentData())
            region.metadata.update(
                font_size=self.font_size.value(), alignment=self.alignment.currentData(),
                color=self._text_color.name(), outline_width=self.outline_width.value(),
            )
            if not self.font_size.value():
                region.metadata.pop("font_size", None)
            if decision is not None:
                region.needs_review = False
                region.metadata["review_decision"] = decision
            item = self._items[region_id]
            item.setPos(self.x_value.value(), self.y_value.value())
            item.setRect(0, 0, self.width_value.value(), self.height_value.value())
            item.setTransformOriginPoint(item.rect().center())
            item.setRotation(self.rotation_value.value())
            self._sync_region(region_id)

        self._record_change("保留原文" if decision == "rejected" else "确认处理" if decision else "修改文字与样式", mutate)
        self.select_region(region_id)

    def begin_mask_stroke(self, *, erase: bool = False) -> None:
        """Start one undoable gesture; direct paint_mask calls remain a low-level API."""
        self.end_mask_stroke()
        if self._mask is None:
            raise RuntimeError("尚未加载页面")
        self._stroke_before = {}
        self._stroke_erasing = erase

    def end_mask_stroke(self) -> None:
        captured, self._stroke_before = self._stroke_before, None
        if captured is None or self._mask is None:
            return
        patches = []
        for rectangle, before in captured.values():
            after = self._mask.copy(rectangle)
            if before != after:
                patches.append((rectangle, before, after))
        if patches:
            self.undo_stack.push(_MaskStrokeCommand(self, patches, erase=self._stroke_erasing))

    def _capture_mask_tiles(self, dirty: QRect, start: tuple[int, int], end: tuple[int, int], radius: int) -> None:
        if self._stroke_before is None or self._mask is None:
            return
        tile = 64
        delta_x, delta_y = end[0] - start[0], end[1] - start[1]
        length_squared = delta_x * delta_x + delta_y * delta_y
        for y in range(dirty.top() // tile, dirty.bottom() // tile + 1):
            for x in range(dirty.left() // tile, dirty.right() // tile + 1):
                key = (x, y)
                if key not in self._stroke_before:
                    rectangle = QRect(x * tile, y * tile, tile, tile).intersected(self._mask.rect())
                    center_x = rectangle.left() + rectangle.width() / 2
                    center_y = rectangle.top() + rectangle.height() / 2
                    projection = (((center_x - start[0]) * delta_x + (center_y - start[1]) * delta_y)
                                  / length_squared) if length_squared else 0.0
                    projection = min(1.0, max(0.0, projection))
                    distance_squared = ((center_x - start[0] - projection * delta_x) ** 2
                                        + (center_y - start[1] - projection * delta_y) ** 2)
                    reach = radius + math.hypot(rectangle.width(), rectangle.height()) / 2 + 1
                    if distance_squared > reach * reach:
                        continue
                    self._stroke_before[key] = (rectangle, self._mask.copy(rectangle))

    def _refresh_mask_overlay(self, dirty: QRect) -> None:
        if self._mask is None or self._mask_visual is None or dirty.isEmpty():
            return
        crop = self._mask.copy(dirty)
        pixels = np.frombuffer(crop.constBits(), dtype=np.uint8).reshape(crop.height(), crop.bytesPerLine())[:, :crop.width()]
        overlay = np.zeros((dirty.height(), dirty.width(), 4), dtype=np.uint8)
        overlay[:, :, :3] = (255, 40, 40)
        overlay[:, :, 3] = np.where(pixels > 0, 110, 0)
        patch = QImage(overlay.data, dirty.width(), dirty.height(), overlay.strides[0], QImage.Format.Format_RGBA8888)
        visual = QPainter(self._mask_visual)
        visual.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        visual.drawImage(dirty.topLeft(), patch)
        visual.end()
        if self._mask_item is not None:
            self._mask_item.update(QRectF(dirty))

    def _restore_mask_patch(self, rectangle: QRect, image: QImage) -> None:
        if self._mask is None:
            return
        painter = QPainter(self._mask)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(rectangle.topLeft(), image)
        painter.end()
        self._refresh_mask_overlay(rectangle)

    def paint_mask(self, x: int, y: int, *, radius: int = 12, erase: bool = False,
                   previous: tuple[int, int] | None = None) -> None:
        if self._mask is None:
            raise RuntimeError("尚未加载页面")
        radius = max(1, int(radius))
        previous_x, previous_y = previous if previous is not None else (x, y)
        dirty = QRect(min(x, previous_x) - radius - 1, min(y, previous_y) - radius - 1,
                      abs(x - previous_x) + radius * 2 + 3, abs(y - previous_y) + radius * 2 + 3).intersected(self._mask.rect())
        if dirty.isEmpty():
            return
        self._capture_mask_tiles(dirty, (previous_x, previous_y), (x, y), radius)
        painter = QPainter(self._mask)
        color = QColor(0, 0, 0) if erase else QColor(255, 255, 255)
        painter.setPen(QPen(color, radius * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(color)
        if previous is not None:
            painter.drawLine(previous_x, previous_y, x, y)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        painter.end()
        self._refresh_mask_overlay(dirty)

    def save_mask(self) -> Path:
        if self._mask is None or self._metadata_path is None:
            raise RuntimeError("尚未加载页面")
        self.end_mask_stroke()
        path = self._metadata_path.with_name(f"{self._metadata_path.stem}_mask.png")
        if not self._mask.save(str(path), "PNG"):
            raise OSError(f"无法保存蒙版：{path}")
        return path

    def save_page(self) -> None:
        if self._metadata_path is None:
            return
        self.apply_controls()
        self._sync_all_regions()
        self._document["regions"] = [asdict(region) for region in self._regions.values()]
        self._document["editor_state"] = {"version": 1, "mask_path": self.save_mask().name}
        self._metadata_path.write_text(json.dumps(self._document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _add_region_item(self, region: TextRegion) -> None:
        self._regions[region.id] = region
        item = RegionItem(region)
        self._items[region.id] = item
        self.scene.addItem(item)

    def _selection_changed(self) -> None:
        self._refresh_review_counter()
        region_id = self.selected_region_id()
        if region_id is None:
            self._set_selection_available(False)
            self.review_status.setText("状态：未选择文字框")
            self.review_status.setStyleSheet("color:#626a77")
            self.render_warning_label.clear()
            self.render_warning_label.hide()
            return
        self._set_selection_available(True)
        self._sync_region(region_id)
        region, item = self._regions[region_id], self._items[region_id]
        self._updating_controls = True
        try:
            self._show_review_status(region)
            self.source_text.setText(region.source_text)
            self.translation_text.setText(region.translated_text)
            self.x_value.setValue(round(item.x()))
            self.y_value.setValue(round(item.y()))
            self.width_value.setValue(round(item.rect().width()))
            self.height_value.setValue(round(item.rect().height()))
            self.rotation_value.setValue(round(item.rotation()))
            self.font_size.setValue(int(region.metadata.get("font_size", 0)))
            self.outline_width.setValue(int(region.metadata.get("outline_width", 1)))
            self._text_color = QColor(str(region.metadata.get("color", "#111111")))
            self._update_color_button()
            self.direction.setCurrentIndex(max(0, self.direction.findData(region.direction)))
            self.alignment.setCurrentIndex(max(0, self.alignment.findData(region.metadata.get("alignment", "center"))))
        finally:
            self._updating_controls = False

    def _show_review_status(self, region: TextRegion) -> None:
        warning = (str(region.metadata.get("render_warning") or "").strip()
                   if region.metadata.get("review_decision") != "rejected" else "")
        self.render_warning_label.setText(f"排版提醒：{warning}" if warning else "")
        self.render_warning_label.setVisible(bool(warning))
        if region.metadata.get("review_decision") == "rejected":
            self.review_status.setText("已选择保留原文：不会擦字，也不会渲染此框的译文。")
            self.review_status.setStyleSheet("color:#626a77")
            return
        if region.metadata.get("review_decision") == "accepted":
            self.review_status.setText("已人工确认：填写译文后保存并重绘。")
            self.review_status.setStyleSheet("color:#bc4729")
            return
        if not region.needs_review:
            self.review_status.setText("状态：可自动处理")
            self.review_status.setStyleSheet("color:#bc4729")
            return
        reason = str(region.metadata.get("auto_processing_excluded_reason", ""))
        reason_labels = {
            "isolated_ascii_alphanumeric": "孤立的半角字母或数字",
            "sound_effect_like": "疑似效果字/短英文数字",
            "punctuation_or_symbol_only": "仅包含标点或符号",
        }
        detail = reason_labels.get(reason, "OCR 置信度较低或需要人工确认")
        self.review_status.setText(f"待人工检查：{detail}；自动翻译、擦字和渲染已跳过")
        self.review_status.setStyleSheet("color:#b42318;font-weight:600")

    def _sync_region(self, region_id: str) -> None:
        region, item = self._regions[region_id], self._items[region_id]
        left, top = round(item.x()), round(item.y())
        right = left + round(item.rect().width())
        bottom = top + round(item.rect().height())
        region.polygon = [(left, top), (right, top), (right, bottom), (left, bottom)]
        region.angle = float(item.rotation())

    def _sync_all_regions(self) -> None:
        for region_id in list(self._regions):
            self._sync_region(region_id)

    def _snapshot(self) -> list[dict]:
        self._sync_all_regions()
        return [asdict(region) for region in self._regions.values()]

    def _restore_snapshot(self, snapshot: list[dict]) -> None:
        selected = self.selected_region_id()
        for item in list(self._items.values()):
            self.scene.removeItem(item)
        self._items.clear()
        self._regions.clear()
        for data in snapshot:
            data = dict(data)
            data["polygon"] = [tuple(point) for point in data["polygon"]]
            self._add_region_item(TextRegion(**data))
        if self._items:
            self.select_region(selected if selected in self._items else next(iter(self._items)))
        self._refresh_review_counter()

    def _commit_drag(self, before: list[dict]) -> None:
        after = self._snapshot()
        if before != after:
            self.undo_stack.push(_SnapshotCommand(self, before, after, "移动文本框"))
        self._selection_changed()

    def _record_change(self, label: str, mutate) -> None:
        before = self._snapshot()
        mutate()
        after = self._snapshot()
        if before == after:
            return
        self._restore_snapshot(before)
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))

    def _choose_text_color(self) -> None:
        color = QColorDialog.getColor(self._text_color, self, "选择文字颜色")
        if color.isValid():
            self._text_color = color
            self._update_color_button()

    def _set_mask_mode(self, mode: str | None) -> None:
        self.end_mask_stroke()
        self.mask_mode = mode
        for button_mode, button in self._mask_buttons.items():
            button.setChecked(button_mode == mode)
        if mode is not None:
            self.preview_mode.setCurrentIndex(0)
            self.mask_overlay.setChecked(True)
        self.view.setDragMode(
            QGraphicsView.DragMode.NoDrag if mode is not None else QGraphicsView.DragMode.ScrollHandDrag
        )
        self.view.setCursor(Qt.CursorShape.CrossCursor if mode is not None else Qt.CursorShape.ArrowCursor)

    def toggle_region_boxes(self) -> None:
        visible = not all(item.isVisible() for item in self._items.values())
        for item in self._items.values():
            item.setVisible(visible)
