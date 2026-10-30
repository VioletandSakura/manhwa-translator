from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.page_editor import PageEditorWidget
from app.ui.theme import stylesheet


def _editor(qtbot, tmp_path: Path, *, review: bool = True, warning: bool = False):
    image = tmp_path / "page.png"
    Image.new("RGB", (800, 600), "white").save(image)
    metadata = tmp_path / "page.json"
    metadata.write_text(json.dumps({"source_path": str(image), "regions": [
        {"id": "first", "polygon": [[80, 80], [240, 80], [240, 150], [80, 150]],
         "source_text": "原文一", "translated_text": "译文一", "confidence": 0.9,
         "needs_review": review, "metadata": {}},
        {"id": "second", "polygon": [[400, 220], [570, 220], [570, 300], [400, 300]],
         "source_text": "原文二", "translated_text": "译文二", "confidence": 0.9,
         "needs_review": False, "metadata": {"render_warning": "文字无法完整放入文本框，请扩大文本框。"} if warning else {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    editor = PageEditorWidget()
    editor.setStyleSheet(stylesheet())
    editor.resize(940, 620)
    qtbot.addWidget(editor)
    editor.show()
    editor.load_page(image, metadata)
    QTest.qWait(15)
    return editor, image, metadata


def _move_held(view, destination):
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(destination),
                        QPointF(view.viewport().mapToGlobal(destination)), Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)


def _stroke(editor, points, *, erase=False):
    editor._set_mask_mode("erase" if erase else "paint")
    first = editor.view.mapFromScene(*points[0])
    QTest.mousePress(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=first)
    for point in points[1:]:
        _move_held(editor.view, editor.view.mapFromScene(*point))
    QTest.mouseRelease(editor.view.viewport(), Qt.MouseButton.LeftButton,
                       pos=editor.view.mapFromScene(*points[-1]))


def test_real_mouse_stroke_is_one_undo_step_with_pixel_exact_redo(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    before = editor._mask.copy()
    _stroke(editor, [(300, 400), (340, 400), (380, 400)])
    after = editor._mask.copy()
    assert editor.undo_stack.count() == 1
    assert all(editor._mask.pixelColor(x, 400).red() == 255 for x in range(300, 381))
    assert editor._regions["first"].needs_review
    editor.undo_stack.undo()
    assert editor._mask == before
    assert editor._mask_visual.pixelColor(340, 400).alpha() == 0
    editor.undo_stack.redo()
    assert editor._mask == after
    assert editor._mask_visual.pixelColor(340, 400).alpha() == 110


def test_eraser_stroke_restores_previous_pixels_without_touching_other_areas(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    editor.paint_mask(400, 400, radius=40)
    editor.paint_mask(700, 500, radius=15)
    before = editor._mask.copy()
    _stroke(editor, [(395, 400), (405, 400)], erase=True)
    erased = editor._mask.copy()
    assert editor._mask.pixelColor(400, 400).red() == 0
    assert editor._mask.pixelColor(700, 500).red() == 255
    editor.undo_stack.undo()
    assert editor._mask == before
    editor.undo_stack.redo()
    assert editor._mask == erased


def test_stroke_history_keeps_only_local_grayscale_patches(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    editor.begin_mask_stroke()
    editor.paint_mask(430, 430, radius=8)
    editor.paint_mask(450, 430, radius=8, previous=(430, 430))
    editor.end_mask_stroke()
    command = editor.undo_stack.command(0)
    assert command.storage_bytes < editor._mask.sizeInBytes() // 10
    assert all(rectangle.width() <= 64 and rectangle.height() <= 64
               for rectangle, _, _ in command.patches)
    editor.undo_stack.undo()
    assert editor._mask.pixelColor(440, 430).red() == 0


def test_painting_and_undo_do_not_convert_full_overlay_to_pixmap(qtbot, tmp_path, monkeypatch):
    editor, _, _ = _editor(qtbot, tmp_path)
    conversions = []
    original = QPixmap.fromImage
    def observed(image, *args, **kwargs):
        conversions.append((image.width(), image.height()))
        return original(image, *args, **kwargs)
    monkeypatch.setattr(QPixmap, "fromImage", staticmethod(observed))
    _stroke(editor, [(300, 400), (340, 400), (380, 400), (420, 400)])
    editor.undo_stack.undo()
    editor.undo_stack.redo()
    QApplication.processEvents()
    assert conversions == []


def test_sparse_diagonal_stroke_does_not_snapshot_its_whole_bounding_rectangle(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    before = editor._mask.copy()
    editor.begin_mask_stroke()
    editor.paint_mask(50, 50, radius=5)
    editor.paint_mask(750, 550, radius=5, previous=(50, 50))
    assert len(editor._stroke_before) < 50  # Bounding rectangle spans over 100 tiles.
    editor.end_mask_stroke()
    after = editor._mask.copy()
    editor.undo_stack.undo()
    assert editor._mask == before
    editor.undo_stack.redo()
    assert editor._mask == after


def test_low_level_mask_api_remains_compatible_and_noop_eraser_uses_no_history(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    editor.paint_mask(100, 400, radius=8)
    assert editor.undo_stack.count() == 0
    editor.begin_mask_stroke(erase=True)
    editor.paint_mask(700, 500, radius=10, erase=True)
    editor.end_mask_stroke()
    assert editor.undo_stack.count() == 0


def test_real_mouse_drag_can_be_undone_and_preserves_source_erasure_geometry(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    editor.select_region("first")
    editor.apply_controls()
    editor.undo_stack.clear()
    before = editor._snapshot()
    start = editor.view.mapFromScene(160, 115)
    end = editor.view.mapFromScene(210, 155)
    QTest.mousePress(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move_held(editor.view, end)
    QTest.mouseRelease(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert editor.undo_stack.count() == 1
    moved = editor._snapshot()
    assert moved[0]["polygon"] != before[0]["polygon"]
    assert moved[0]["metadata"]["source_polygon"] == before[0]["metadata"]["source_polygon"]
    assert moved[0]["needs_review"] is True
    editor.undo_stack.undo()
    assert editor._snapshot() == before
    editor.undo_stack.redo()
    assert editor._snapshot() == moved


def test_mask_and_geometry_commands_share_one_correct_undo_order(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path)
    editor.select_region("first")
    editor.update_selected_geometry(90, 90, 180, 80, 0)
    geometry = editor._snapshot()
    _stroke(editor, [(300, 400)])
    editor.undo_stack.undo()
    assert editor._mask.pixelColor(300, 400).red() == 0
    assert editor._snapshot() == geometry
    editor.undo_stack.undo()
    assert editor._regions["first"].bounds == (80, 80, 240, 150)
    editor.undo_stack.redo()
    editor.undo_stack.redo()
    assert editor._mask.pixelColor(300, 400).red() == 255
    assert editor._snapshot() == geometry


def test_next_review_cycles_warnings_and_low_confidence_without_accepting(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path, warning=True)
    assert editor.pending_review_count == 2
    assert editor.review_counter.text() == "待检查 2 处"
    assert editor.next_review_region() == "first"
    editor.translation_text.setText("尚未点击应用的修改")
    assert editor.next_review_region() == "second"
    assert editor._regions["first"].translated_text == "尚未点击应用的修改"
    assert editor._regions["first"].needs_review
    assert not editor.render_warning_label.isHidden()
    assert "无法完整放入" in editor.render_warning_label.text()
    assert editor.next_review_region() == "first"
    assert editor.render_warning_label.isHidden()


def test_empty_review_list_emits_cross_page_signal_and_f8_works(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path, review=False)
    requested = []
    editor.next_review_page_requested.connect(lambda: requested.append(True))
    editor.activateWindow()
    editor.view.setFocus()
    QTest.qWait(30)
    QTest.keyClick(editor.view, Qt.Key.Key_F8)
    qtbot.waitUntil(lambda: requested == [True], timeout=1000)
    assert editor.pending_review_count == 0


def test_next_review_scrolls_whole_wrapped_warning_into_narrow_inspector(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path, review=False, warning=True)
    editor.resize(760, 540)
    assert editor.next_review_region() == "second"
    viewport = editor._inspector_scroll.viewport()
    warning = editor.render_warning_label
    qtbot.waitUntil(lambda: viewport.rect().contains(warning.mapTo(viewport, warning.rect().topLeft()))
                    and viewport.rect().contains(warning.mapTo(viewport, warning.rect().bottomRight())), timeout=1000)


def test_render_refresh_updates_and_clears_warning_without_overwriting_typed_text(qtbot, tmp_path):
    editor, image, metadata = _editor(qtbot, tmp_path, review=False)
    editor.select_region("second")
    editor.translation_text.setText("未保存的新译文")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["regions"][1]["metadata"] = {"render_warning": "字号过小，请调整", "rendered_font_size": 8}
    metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    editor.show_rendered_preview(image)
    assert editor.pending_review_count == 1
    assert "字号过小" in editor.render_warning_label.text()
    assert editor.translation_text.text() == "未保存的新译文"
    payload["regions"][1]["metadata"] = {"rendered_font_size": 18}
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    editor.show_rendered_preview(image)
    assert editor.pending_review_count == 0
    assert editor.render_warning_label.isHidden()


def test_review_count_tracks_undo_and_loading_another_page_clears_mask_history(qtbot, tmp_path):
    editor, image, metadata = _editor(qtbot, tmp_path)
    editor.add_region()
    assert editor.pending_review_count == 2
    editor.undo_stack.undo()
    assert editor.pending_review_count == 1
    editor.undo_stack.redo()
    assert editor.pending_review_count == 2
    _stroke(editor, [(400, 400)])
    editor.load_page(image, metadata)
    assert not editor.undo_stack.canUndo()
    assert editor.pending_review_count == 1
    assert editor._mask.pixelColor(400, 400).red() == 0


def test_rejected_region_with_old_warning_is_not_a_review_target(qtbot, tmp_path):
    editor, _, _ = _editor(qtbot, tmp_path, review=False, warning=True)
    editor._regions["second"].metadata["review_decision"] = "rejected"
    editor.select_region("second")
    assert editor.pending_review_count == 0
    assert editor.render_warning_label.isHidden()
    requested = []
    editor.next_review_page_requested.connect(lambda: requested.append(True))
    assert editor.next_review_region() is None
    assert requested == [True]


def test_preview_refresh_preserves_new_document_cache_fields_on_save_and_drops_missing_keys(qtbot, tmp_path):
    editor, image, metadata = _editor(qtbot, tmp_path, review=False)
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    cache = {"version": 1, "signature": "verified-test-signature"}
    payload.update(inpaint_cache=cache, original_available=True)
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    editor.show_rendered_preview(image)
    editor.save_page()
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert saved["inpaint_cache"] == cache
    assert saved["original_available"] is True
    saved.pop("inpaint_cache")
    saved.pop("original_available")
    metadata.write_text(json.dumps(saved), encoding="utf-8")
    editor.show_rendered_preview(image)
    editor.save_page()
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert "inpaint_cache" not in saved
    assert "original_available" not in saved
