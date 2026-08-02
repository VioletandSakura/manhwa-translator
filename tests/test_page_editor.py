from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.ui.page_editor import PageEditorWidget


def _page_files(tmp_path: Path) -> tuple[Path, Path]:
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 120), "white").save(image)
    metadata = tmp_path / "page.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_path": str(image),
                "regions": [
                    {
                        "id": "r1",
                        "polygon": [[10, 10], [40, 10], [40, 30], [10, 30]],
                        "source_text": "原文",
                        "translated_text": "译文",
                        "confidence": 0.9,
                        "direction": "horizontal",
                        "angle": 0,
                        "needs_review": False,
                        "metadata": {},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return image, metadata


def test_editor_modifies_geometry_text_and_persists_state(tmp_path: Path, qtbot) -> None:
    image, metadata = _page_files(tmp_path)
    editor = PageEditorWidget()
    qtbot.addWidget(editor)
    editor.load_page(image, metadata)

    editor.select_region("r1")
    editor.set_selected_translation("新的译文")
    editor.update_selected_geometry(15, 18, 50, 25, 12)
    editor.duplicate_selected()
    assert editor.region_count == 2
    editor.undo_stack.undo()
    assert editor.region_count == 1
    editor.undo_stack.redo()
    assert editor.region_count == 2
    editor.save_page()

    saved = json.loads(metadata.read_text(encoding="utf-8"))
    first = next(region for region in saved["regions"] if region["id"] == "r1")
    assert first["translated_text"] == "新的译文"
    assert first["angle"] == 12
    assert saved["editor_state"]["version"] == 1


def test_editor_can_save_local_mask(tmp_path: Path, qtbot) -> None:
    image, metadata = _page_files(tmp_path)
    editor = PageEditorWidget()
    qtbot.addWidget(editor)
    editor.load_page(image, metadata)

    editor.paint_mask(25, 25, radius=8)
    mask_path = editor.save_mask()

    assert mask_path.is_file()
    assert Image.open(mask_path).getbbox() is not None


def test_editor_highlights_review_region_and_shows_chinese_reason(tmp_path: Path, qtbot) -> None:
    image, metadata = _page_files(tmp_path)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["regions"][0]["needs_review"] = True
    document["regions"][0]["metadata"] = {
        "auto_processing_excluded_reason": "isolated_ascii_alphanumeric"
    }
    metadata.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    editor = PageEditorWidget()
    qtbot.addWidget(editor)

    editor.load_page(image, metadata)
    editor.select_region("r1")

    assert editor._items["r1"].pen().color().name() == "#dc2626"
    assert "待人工检查" in editor.review_status.text()
    assert "孤立的半角字母或数字" in editor.review_status.text()
