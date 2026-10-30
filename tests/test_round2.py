import asyncio
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.domain.models import TextRegion
from app.paths import AppPaths
from app.rendering.pillow_renderer import PillowTextRenderer
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.tasks.pipeline import PipelineOptions
from app.translation.settings import TranslationSettings, parse_pairs
from app.ui.page_editor import PageEditorWidget
from test_application_services import MemoryCredentialBackend


def application(tmp_path):
    result = TranslationApplication(AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp"),
                                    credentials=CredentialStore(MemoryCredentialBackend()))
    result.initialize()
    return result


@pytest.mark.parametrize("category,payload", [
    ("prompt", "请使用简洁的口语。"), ("glossary", "Hero\t勇者"), ("replacement", "同伴\t伙伴"),
])
def test_saved_rules_reach_provider_and_have_separate_caches(tmp_path, monkeypatch, category, payload):
    import app.services.application as service
    calls = []

    class Provider:
        def __init__(self, config):
            self.config = config

        async def translate(self, texts, source, target):
            calls.append(self.config.system_prompt)
            return ["同伴"] * len(texts)

        async def close(self):
            pass

    monkeypatch.setattr(service, "OpenAICompatibleTranslator", Provider)
    app = application(tmp_path)
    app.save_api_channel({"id": "deepseek-default", "service_name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                          "model": "deepseek-flash", "api_key": "test-local-key"})
    app.database.set_api_channel_health("deepseek-default", verified=True, last_error=None)

    async def translate():
        translator = app.build_translator()
        try:
            return await translator.translate(["Hero"], "en", "zh-Hans")
        finally:
            await translator.close()

    assert asyncio.run(translate()) == ["同伴"]
    assert asyncio.run(translate()) == ["同伴"]
    assert len(calls) == 1
    app.save_library_content(category, "当前", payload)
    assert asyncio.run(translate()) == (["伙伴"] if category == "replacement" else ["同伴"])
    assert len(calls) == 2
    assert asyncio.run(translate()) == (["伙伴"] if category == "replacement" else ["同伴"])
    assert len(calls) == 2
    if category == "prompt":
        assert payload in calls[-1]
    if category == "glossary":
        assert '"Hero": "勇者"' in calls[-1]
    app.database.close()


def test_rule_parser_supports_literal_deletion_and_rejects_ambiguous_rows():
    pairs = parse_pairs("# comment\n[x]\t\n...\t……", glossary=False)
    assert TranslationSettings(replacements=pairs).apply_replacements("[x]你好...") == "你好……"
    with pytest.raises(ValueError, match="第1行"):
        parse_pairs("没有Tab", glossary=True)
    with pytest.raises(ValueError, match="重复"):
        parse_pairs("Hero\t勇者\nHero\t英雄", glossary=True)


def render(region, **settings):
    return asyncio.run(PillowTextRenderer(**settings).render(np.full((240, 420, 3), 255, np.uint8), [region]))


def text_region(**kwargs):
    return TextRegion(id="one", polygon=[(40, 40), (380, 40), (380, 200), (40, 200)],
                      translated_text="Sample", **kwargs)


def ink_bounds(image):
    ys, xs = np.where(np.any(image < 200, axis=2))
    return xs.min(), ys.min(), xs.max(), ys.max()


def test_global_size_and_region_color_alignment_and_rotation_change_pixels():
    small = text_region()
    large = text_region()
    a = render(small, min_font_size=12, max_font_size=12)
    b = render(large, min_font_size=36, max_font_size=36)
    assert ink_bounds(b)[3] - ink_bounds(b)[1] > ink_bounds(a)[3] - ink_bounds(a)[1]
    assert small.metadata["rendered_font_size"] == 12
    assert large.metadata["rendered_font_size"] == 36
    left = render(text_region(metadata={"font_size": 24, "alignment": "left", "color": "#ff0000"}))
    right = render(text_region(metadata={"font_size": 24, "alignment": "right"}))
    assert ink_bounds(left)[0] < ink_bounds(right)[0]
    assert np.count_nonzero((left[:, :, 2] > 200) & (left[:, :, 1] < 100) & (left[:, :, 0] < 100)) > 50
    rotated = render(text_region(angle=90, metadata={"font_size": 24}))
    normal = render(text_region(metadata={"font_size": 24}))
    n, r = ink_bounds(normal), ink_bounds(rotated)
    assert abs((n[2] - n[0]) - (r[3] - r[1])) <= 2
    assert abs((n[3] - n[1]) - (r[2] - r[0])) <= 2


def test_outline_is_visible_on_dark_background():
    background = np.zeros((240, 420, 3), np.uint8)
    region = text_region(metadata={"font_size": 36, "color": "#ff0000", "outline_width": 3})
    result = asyncio.run(PillowTextRenderer().render(background, [region]))
    assert np.count_nonzero(np.all(result > 230, axis=2)) > 100
    assert np.count_nonzero((result[:, :, 2] > 200) & (result[:, :, 0] < 100)) > 100


def test_selected_font_file_changes_the_rendered_glyphs():
    arial, mono = Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/consola.ttf")
    if not arial.is_file() or not mono.is_file():
        pytest.skip("Windows sample fonts unavailable")
    a = render(text_region(metadata={"font_size": 36}), font_path=arial)
    b = render(text_region(metadata={"font_size": 36}), font_path=mono)
    assert not np.array_equal(a, b)


def test_render_settings_validate_and_survive_restart_and_preset(tmp_path):
    app = application(tmp_path)
    options = PipelineOptions(translate=False, min_font_size=18, max_font_size=32)
    app.save_render_settings(options)
    assert app.load_render_settings() == options.render_settings()
    app.database.close()
    reopened = TranslationApplication(app.paths, credentials=CredentialStore(MemoryCredentialBackend()))
    assert reopened.load_render_settings()["max_font_size"] == 32
    with pytest.raises(ValueError, match="字号范围"):
        PipelineOptions(min_font_size=50, max_font_size=12)
    with pytest.raises(ValueError, match="字体"):
        PipelineOptions(font_path=str(tmp_path / "missing.ttf"))
    reopened.database.close()


def page_fixture(tmp_path):
    output = tmp_path / "page-output"
    for name in ("metadata", "inpainted", "translated"):
        (output / name).mkdir(parents=True)
    source = np.full((200, 300, 3), 255, np.uint8)
    source[40:75, 30:110] = 0
    source[40:75, 180:240] = 0
    source[145:155, 215:225] = 0
    original = tmp_path / "page.png"
    Image.fromarray(source).save(original)
    with zipfile.ZipFile(output / "originals.zip", "w") as archive:
        archive.write(original, "page.png")
    metadata = output / "metadata/page.json"
    document = {"source_path": str(original), "regions": [
        {"id": "dialogue", "polygon": [[20, 30], [120, 30], [120, 85], [20, 85]],
         "source_text": "HELLO FRIEND", "translated_text": "你好", "needs_review": False},
        {"id": "effect", "polygon": [[170, 30], [250, 30], [250, 85], [170, 85]],
         "source_text": "!!", "translated_text": "不应覆盖", "needs_review": True},
    ]}
    metadata.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    Image.new("RGB", (300, 200), "white").save(output / "inpainted/page_inpainted.png")
    return source, original, metadata, document


def test_masks_affect_output_but_protect_review_regions_and_rejection_restores_original(tmp_path):
    app = application(tmp_path)
    source, original, metadata, document = page_fixture(tmp_path)
    mask = np.zeros(source.shape[:2], np.uint8)
    mask[135:165, 205:235] = 255
    mask[30:85, 170:250] = 255
    Image.fromarray(mask).save(metadata.with_name("page_mask.png"))
    document["editor_state"] = {"mask_path": "page_mask.png"}
    metadata.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    original.unlink()  # The archived output copy must be sufficient.
    result_path = asyncio.run(app.rerender_metadata(metadata))
    result = np.asarray(Image.open(result_path))
    assert np.array_equal(result[30:85, 170:250], source[30:85, 170:250])
    assert result[145:155, 215:225].mean() > 230
    assert not np.array_equal(result[30:85, 20:120], source[30:85, 20:120])
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    saved["regions"][0]["metadata"]["review_decision"] = "rejected"
    Image.fromarray(np.zeros(mask.shape, np.uint8)).save(metadata.with_name("page_mask.png"))
    metadata.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    result_path = asyncio.run(app.rerender_metadata(metadata))
    assert np.array_equal(np.asarray(Image.open(result_path)), source)
    app.database.close()


def test_editor_mask_reopens_and_review_decision_is_explicit(tmp_path, qtbot):
    source, original, metadata, document = page_fixture(tmp_path)
    editor = PageEditorWidget()
    qtbot.addWidget(editor)
    editor.load_page(original, metadata)
    editor.select_region("effect")
    editor.translation_text.setText("人工译文")
    editor.apply_controls()
    assert editor._regions["effect"].needs_review
    for label, expected in (("确认处理", "accepted"), ("保留原文", "rejected")):
        button = next(b for b in editor.findChildren(QPushButton) if b.text() == label)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert editor._regions["effect"].metadata["review_decision"] == expected
    editor.paint_mask(220, 150, radius=15)
    editor.paint_mask(140, 175, radius=5, previous=(40, 175))
    editor.undo_stack.undo()
    assert editor._regions["effect"].metadata["review_decision"] == "accepted"
    editor.undo_stack.redo()
    assert editor._regions["effect"].metadata["review_decision"] == "rejected"
    editor.save_page()
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert saved["editor_state"]["mask_path"] == "page_mask.png"
    reopened = PageEditorWidget()
    qtbot.addWidget(reopened)
    reopened.load_page(original, metadata)
    assert reopened._mask.pixelColor(220, 150).red() == 255
    assert all(reopened._mask.pixelColor(x, 175).red() == 255 for x in range(40, 141))
    reopened.paint_mask(220, 150, radius=16, erase=True)
    reopened.save_page()
    editor.load_page(original, metadata)
    assert editor._mask.pixelColor(220, 150).red() == 0


def test_bad_mask_does_not_replace_the_loaded_editor_page(tmp_path, qtbot):
    source, original, metadata, document = page_fixture(tmp_path)
    editor = PageEditorWidget()
    qtbot.addWidget(editor)
    editor.load_page(original, metadata)
    bad = metadata.with_name("bad.json")
    document["editor_state"] = {"mask_path": "wrong-size.png"}
    Image.new("L", (5, 5)).save(metadata.with_name("wrong-size.png"))
    bad.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="尺寸"):
        editor.load_page(original, bad)
    assert editor.metadata_path == metadata
    assert editor.region_count == 2


def test_moving_translation_box_does_not_move_or_enlarge_source_erasure(tmp_path):
    app = application(tmp_path)
    source, original, metadata, document = page_fixture(tmp_path)
    region = document["regions"][0]
    region["metadata"] = {"source_polygon": region["polygon"]}
    region["polygon"] = [[200, 130], [260, 130], [260, 175], [200, 175]]
    metadata.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    asyncio.run(app.rerender_metadata(metadata))
    cleaned = np.asarray(Image.open(metadata.parent.parent / "inpainted/page_inpainted.png"))
    assert cleaned[45:70, 40:100].mean() > 230
    assert np.array_equal(cleaned[145:155, 215:225], source[145:155, 215:225])
    app.database.close()
