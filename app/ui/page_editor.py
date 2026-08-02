from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QMouseEvent, QPainter, QPen, QPixmap, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.domain.models import TextRegion


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
        self.setPen(QPen(QColor("#1570ef"), 2, Qt.PenStyle.DashLine))
        self.setBrush(QColor(21, 112, 239, 24))


class MaskGraphicsView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, editor: "PageEditorWidget") -> None:
        super().__init__(scene)
        self.editor = editor

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.editor.mask_mode is not None:
            self._paint(event)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self.editor.mask_mode is not None:
            self._paint(event)
            return
        super().mouseMoveEvent(event)

    def _paint(self, event: QMouseEvent) -> None:
        point = self.mapToScene(event.position().toPoint())
        self.editor.paint_mask(
            round(point.x()), round(point.y()), radius=self.editor.mask_radius.value(),
            erase=self.editor.mask_mode == "erase",
        )


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


class PageEditorWidget(QWidget):
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
        self._mask_item: QGraphicsPixmapItem | None = None
        self.mask_mode: str | None = None
        self._text_color = QColor("#111111")
        self._updating_controls = False
        self._build_ui()
        self.scene.selectionChanged.connect(self._selection_changed)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo_stack.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.undo_stack.redo)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save_page)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.duplicate_selected)
        QShortcut(QKeySequence.StandardKey.Delete, self, activated=self.delete_selected)

    @property
    def region_count(self) -> int:
        return len(self._regions)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.addWidget(self.view, 1)
        panel = QWidget()
        panel.setMaximumWidth(340)
        side = QVBoxLayout(panel)
        side.addWidget(QLabel("文本框属性"))
        form = QFormLayout()
        self.source_text = QLineEdit()
        self.translation_text = QLineEdit()
        self.x_value = self._geometry_spinbox(-100_000, 100_000)
        self.y_value = self._geometry_spinbox(-100_000, 100_000)
        self.width_value = self._geometry_spinbox(1, 100_000)
        self.height_value = self._geometry_spinbox(1, 100_000)
        self.rotation_value = self._geometry_spinbox(-180, 180)
        self.font_size = self._geometry_spinbox(6, 300)
        self.font_size.setValue(24)
        self.outline_width = self._geometry_spinbox(0, 20)
        self.outline_width.setValue(1)
        self.color_button = QPushButton("选择颜色")
        self.color_button.clicked.connect(self._choose_text_color)
        self.direction = QComboBox()
        self.direction.addItem("横排", "horizontal")
        self.direction.addItem("竖排", "vertical")
        self.alignment = QComboBox()
        self.alignment.addItem("居中", "center")
        self.alignment.addItem("左对齐", "left")
        self.alignment.addItem("右对齐", "right")
        form.addRow("原文", self.source_text)
        form.addRow("译文", self.translation_text)
        form.addRow("X", self.x_value)
        form.addRow("Y", self.y_value)
        form.addRow("宽", self.width_value)
        form.addRow("高", self.height_value)
        form.addRow("旋转", self.rotation_value)
        form.addRow("字号", self.font_size)
        form.addRow("文字颜色", self.color_button)
        form.addRow("描边宽度", self.outline_width)
        form.addRow("方向", self.direction)
        form.addRow("对齐", self.alignment)
        side.addLayout(form)
        apply_button = QPushButton("应用属性")
        apply_button.clicked.connect(self.apply_controls)
        side.addWidget(apply_button)
        actions = QHBoxLayout()
        for label, callback in (
            ("添加", self.add_region),
            ("复制", self.duplicate_selected),
            ("删除", self.delete_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        side.addLayout(actions)
        history = QHBoxLayout()
        undo_button = QPushButton("撤销")
        redo_button = QPushButton("重做")
        undo_button.clicked.connect(self.undo_stack.undo)
        redo_button.clicked.connect(self.undo_stack.redo)
        history.addWidget(undo_button)
        history.addWidget(redo_button)
        side.addLayout(history)
        mask_actions = QHBoxLayout()
        brush_button = QPushButton("蒙版画笔")
        eraser_button = QPushButton("橡皮擦")
        stop_mask_button = QPushButton("结束绘制")
        brush_button.clicked.connect(lambda: self._set_mask_mode("paint"))
        eraser_button.clicked.connect(lambda: self._set_mask_mode("erase"))
        stop_mask_button.clicked.connect(lambda: self._set_mask_mode(None))
        mask_actions.addWidget(brush_button)
        mask_actions.addWidget(eraser_button)
        mask_actions.addWidget(stop_mask_button)
        side.addLayout(mask_actions)
        self.mask_radius = self._geometry_spinbox(1, 200)
        self.mask_radius.setValue(16)
        side.addWidget(self.mask_radius)
        toggle_boxes = QPushButton("显示/隐藏检测框")
        toggle_boxes.clicked.connect(self.toggle_region_boxes)
        side.addWidget(toggle_boxes)
        save_button = QPushButton("保存编辑状态")
        save_button.clicked.connect(self.save_page)
        side.addWidget(save_button)
        side.addStretch(1)
        root.addWidget(panel)

    @staticmethod
    def _geometry_spinbox(minimum: int, maximum: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(minimum, maximum)
        return box

    def load_page(self, image_path: Path, metadata_path: Path) -> None:
        image_path, metadata_path = Path(image_path), Path(metadata_path)
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(document.get("regions"), list):
            raise ValueError("页面JSON缺少regions数组")
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise ValueError(f"无法加载编辑图片：{image_path}")
        self.scene.clear()
        self._items.clear()
        self._regions.clear()
        self._document = document
        self._image_path = image_path
        self._metadata_path = metadata_path
        self._background = self.scene.addPixmap(pixmap)
        self._background.setZValue(-100)
        self.scene.setSceneRect(QRectF(pixmap.rect()))
        self._mask = QImage(pixmap.size(), QImage.Format.Format_Grayscale8)
        self._mask.fill(0)
        self._mask_visual = QImage(pixmap.size(), QImage.Format.Format_ARGB32)
        self._mask_visual.fill(Qt.GlobalColor.transparent)
        self._mask_item = self.scene.addPixmap(QPixmap.fromImage(self._mask_visual))
        self._mask_item.setZValue(50)
        for data in document["regions"]:
            region = TextRegion(
                id=str(data["id"]),
                polygon=[(int(point[0]), int(point[1])) for point in data["polygon"]],
                source_text=str(data.get("source_text", "")),
                translated_text=str(data.get("translated_text", "")),
                confidence=float(data.get("confidence", 0)),
                direction=str(data.get("direction", "horizontal")),
                angle=float(data.get("angle", 0)),
                needs_review=bool(data.get("needs_review", False)),
                metadata=dict(data.get("metadata", {})),
            )
            self._add_region_item(region)
        self.undo_stack.clear()
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def select_region(self, region_id: str) -> None:
        self.scene.clearSelection()
        self._items[region_id].setSelected(True)

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
        if self._updating_controls:
            return
        region_id = self.selected_region_id()
        if region_id is None:
            return
        self._regions[region_id].source_text = self.source_text.text()
        self._regions[region_id].translated_text = self.translation_text.text()
        self._regions[region_id].direction = str(self.direction.currentData())
        self._regions[region_id].metadata["font_size"] = self.font_size.value()
        self._regions[region_id].metadata["alignment"] = self.alignment.currentData()
        self._regions[region_id].metadata["color"] = self._text_color.name()
        self._regions[region_id].metadata["outline_width"] = self.outline_width.value()
        self.update_selected_geometry(
            self.x_value.value(), self.y_value.value(), self.width_value.value(),
            self.height_value.value(), self.rotation_value.value(),
        )

    def paint_mask(self, x: int, y: int, *, radius: int = 12, erase: bool = False) -> None:
        if self._mask is None:
            raise RuntimeError("尚未加载页面")
        painter = QPainter(self._mask)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0 if erase else 255, 0 if erase else 255, 0 if erase else 255))
        painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
        painter.end()
        if self._mask_visual is not None:
            visual = QPainter(self._mask_visual)
            visual.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
                if erase
                else QPainter.CompositionMode.CompositionMode_SourceOver
            )
            visual.setPen(Qt.PenStyle.NoPen)
            visual.setBrush(QColor(255, 40, 40, 110))
            visual.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
            visual.end()
            if self._mask_item is not None:
                self._mask_item.setPixmap(QPixmap.fromImage(self._mask_visual))

    def save_mask(self) -> Path:
        if self._mask is None or self._metadata_path is None:
            raise RuntimeError("尚未加载页面")
        path = self._metadata_path.with_name(f"{self._metadata_path.stem}_mask.png")
        if not self._mask.save(str(path), "PNG"):
            raise OSError(f"无法保存蒙版：{path}")
        return path

    def save_page(self) -> None:
        if self._metadata_path is None:
            return
        self._sync_all_regions()
        self._document["regions"] = [asdict(region) for region in self._regions.values()]
        self._document["editor_state"] = {"version": 1, "mask_path": str(self.save_mask())}
        self._metadata_path.write_text(json.dumps(self._document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _add_region_item(self, region: TextRegion) -> None:
        self._regions[region.id] = region
        item = RegionItem(region)
        self._items[region.id] = item
        self.scene.addItem(item)

    def _selection_changed(self) -> None:
        region_id = self.selected_region_id()
        if region_id is None:
            return
        self._sync_region(region_id)
        region, item = self._regions[region_id], self._items[region_id]
        self._updating_controls = True
        try:
            self.source_text.setText(region.source_text)
            self.translation_text.setText(region.translated_text)
            self.x_value.setValue(round(item.x()))
            self.y_value.setValue(round(item.y()))
            self.width_value.setValue(round(item.rect().width()))
            self.height_value.setValue(round(item.rect().height()))
            self.rotation_value.setValue(round(item.rotation()))
            self.font_size.setValue(int(region.metadata.get("font_size", 24)))
            self.outline_width.setValue(int(region.metadata.get("outline_width", 1)))
            self._text_color = QColor(str(region.metadata.get("color", "#111111")))
            self.color_button.setStyleSheet(f"background:{self._text_color.name()};color:white")
            self.direction.setCurrentIndex(max(0, self.direction.findData(region.direction)))
            self.alignment.setCurrentIndex(max(0, self.alignment.findData(region.metadata.get("alignment", "center"))))
        finally:
            self._updating_controls = False

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
        for item in list(self._items.values()):
            self.scene.removeItem(item)
        self._items.clear()
        self._regions.clear()
        for data in snapshot:
            data = dict(data)
            data["polygon"] = [tuple(point) for point in data["polygon"]]
            self._add_region_item(TextRegion(**data))

    def _record_change(self, label: str, mutate) -> None:
        before = self._snapshot()
        mutate()
        after = self._snapshot()
        self._restore_snapshot(before)
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))

    def _choose_text_color(self) -> None:
        color = QColorDialog.getColor(self._text_color, self, "选择文字颜色")
        if color.isValid():
            self._text_color = color
            self.color_button.setStyleSheet(f"background:{color.name()};color:white")

    def _set_mask_mode(self, mode: str | None) -> None:
        self.mask_mode = mode
        self.view.setDragMode(
            QGraphicsView.DragMode.NoDrag if mode is not None else QGraphicsView.DragMode.ScrollHandDrag
        )

    def toggle_region_boxes(self) -> None:
        visible = not all(item.isVisible() for item in self._items.values())
        for item in self._items.values():
            item.setVisible(visible)
