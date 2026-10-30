"""Verify round-two settings through real Qt controls and inspectable images.

--live uses the saved DeepSeek credential for two small generated-text requests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QPushButton, QScrollArea

from app.domain.enums import PageStatus, TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.translation.openai_compatible import OpenAICompatibleTranslator
from app.ui.controller import MainController
from app.ui.main_window import MainWindow
from scripts.qa_desktop import EphemeralCredentials, load_live_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Use a new output directory to preserve existing evidence")
    root.mkdir(parents=True, exist_ok=True)
    report = {"mode": "live" if args.live else "offline", "errors": [], "api_calls": [], "checks": {}}
    qt = QApplication.instance() or QApplication([])
    app = TranslationApplication(AppPaths(root / "runtime", root / "results", root / "temp"),
                                 credentials=CredentialStore(EphemeralCredentials(load_live_key(None) if args.live else None)))
    app.initialize()
    if args.live:
        key = load_live_key(None)
        app.save_api_channel({"id": "deepseek-default", "service_name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                              "model": "deepseek-flash", "api_key": key, "enabled": True, "retries": 0})
        asyncio.run(app.test_api_channel("deepseek-default"))
    window = MainWindow(app.paths)
    controller = MainController(app, window)
    window.show_error = report["errors"].append
    window.show()
    qt.processEvents()

    def click(label):
        button = next(b for b in window.findChildren(QPushButton) if b.text() == label)
        for index in range(window.tabs.count()):
            page = window.tabs.widget(index)
            if page is button or page.isAncestorOf(button):
                window.tabs.setCurrentIndex(index)
                break
        # Activate the new page layout before mapping a click into its viewport.
        qt.processEvents()
        QTest.qWait(30)
        ancestors = []
        parent = button.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                ancestors.append(parent)
            parent = parent.parentWidget()
        for scroll in reversed(ancestors):
            if scroll.widget() is not None and scroll.widget().isAncestorOf(button):
                scroll.ensureWidgetVisible(button)
        qt.processEvents()
        QTest.qWait(20)
        assert button.isVisible() and button.isEnabled(), label
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        qt.processEvents()

    def wait():
        deadline = time.monotonic() + 60
        while controller._threads and time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.02)
        assert not controller._threads and not report["errors"], report["errors"]

    def redraw(name):
        click("保存并重绘译图")
        wait()
        target = root / "results" / "demo" / "translated" / "sample_translated.png"
        shutil.copyfile(target, root / f"{name}.png")
        return np.asarray(Image.open(target))

    window.tabs.setCurrentIndex(5)
    window.glossary_editor.setPlainText("Velora\t洛薇")
    window.prompt_editor.setPlainText("你是漫画翻译员。忠实翻译，每条译文必须以‘旁白：’开头，遵守术语表，只输出JSON。")
    click("保存术语表与提示词")
    window.tabs.setCurrentIndex(6)
    window.replacement_editor.setPlainText("旁白：\t对白：")
    click("保存替换规则")
    translation = "对白：洛薇说你好。"
    if args.live:
        request = OpenAICompatibleTranslator._request

        async def observed(translator, payload):
            response = await request(translator, {**payload, "max_tokens": 1024})
            body = response.json()
            report["api_calls"].append({"model": body.get("model"), "usage": body.get("usage"),
                                        "finish_reason": body["choices"][0].get("finish_reason")})
            return response

        async def translate():
            translator = app.build_translator()
            try:
                return (await translator.translate(["Velora says hello."], "en", "zh-Hans"))[0]
            finally:
                await translator.close()

        with patch.object(OpenAICompatibleTranslator, "_request", observed):
            translation = asyncio.run(translate())
            assert translation.startswith("对白：") and "洛薇" in translation
            assert asyncio.run(translate()) == translation and len(report["api_calls"]) == 1
            window.tabs.setCurrentIndex(5)
            window.glossary_editor.setPlainText("Velora\t薇拉")
            click("保存术语表与提示词")
            changed = asyncio.run(translate())
            assert "薇拉" in changed and changed.startswith("对白：") and len(report["api_calls"]) == 2
            window.glossary_editor.setPlainText("Velora\t洛薇")
            click("保存术语表与提示词")
            assert asyncio.run(translate()) == translation and len(report["api_calls"]) == 2
        report["policy_results"] = {"first": translation, "changed_glossary": changed}
        report["checks"]["live_rules_and_separate_reusable_caches"] = True

    source = root / "sample.png"
    picture = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((20, 20, 880, 580), outline="black", width=3)
    draw.ellipse((130, 140, 770, 370), fill="#d5dee8", outline="black", width=3)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 38)
    draw.text((450, 250), "Velora says hello.", font=font, anchor="mm", fill="black")
    text_bounds = draw.textbbox((450, 250), "Velora says hello.", font=font, anchor="mm")
    x1, y1, x2, y2 = text_bounds
    source_polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    draw.text((85, 90), "BAM", font=font, fill="black")
    draw.ellipse((750, 465, 770, 485), fill="black")
    picture.save(source)
    original_pixels = np.asarray(picture).copy()
    output = root / "results/demo"
    batch = app.create_batch_task([source], output)
    for folder in ("original", "inpainted", "translated", "metadata"):
        (output / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output / "original/sample.png")
    shutil.copyfile(source, output / "inpainted/sample_inpainted.png")
    shutil.copyfile(source, output / "translated/sample_translated.png")
    metadata = output / "metadata/sample.json"
    metadata.write_text(json.dumps({"source_path": str(source), "processing_mode": "ocr_only", "regions": [
        {"id": "dialogue", "polygon": [[200, 180], [700, 180], [700, 330], [200, 330]],
         "source_text": "Velora says hello.", "translated_text": translation, "needs_review": False,
         "metadata": {"source_polygon": source_polygon}},
        {"id": "effect", "polygon": [[70, 75], [185, 75], [185, 145], [70, 145]],
         "source_text": "BAM", "translated_text": "效果字", "needs_review": True,
         "metadata": {"auto_processing_excluded_reason": "sound_effect_like"}},
    ]}, ensure_ascii=False), encoding="utf-8")
    pages = app.database.list_task_pages(batch.task_id)
    app.database.set_page_result(pages[0].id, str(output / "translated/sample_translated.png"))
    app.database.update_page_status(pages[0].id, PageStatus.COMPLETED, stage="保存", progress=100)
    app.database.update_task_status(batch.task_id, TaskStatus.COMPLETED)
    app.archive_originals(batch.task_id)
    source.unlink()  # Force editing to use the immutable archived copy.
    controller._active_task_id = batch.task_id
    controller._active_page_ids = [pages[0].id]
    window.set_queue_pages([source])
    window.queue_table.selectRow(0)

    window.tabs.setCurrentIndex(4)
    window.font_path.setText("C:/Windows/Fonts/msyh.ttc")
    window.min_font_size.setValue(18)
    window.max_font_size.setValue(32)
    click("保存字体设置")
    assert app.load_render_settings()["max_font_size"] == 32
    window.tabs.setCurrentIndex(7)
    window.preset_name.setText("第二轮字体样例")
    click("保存当前设置为预设")
    window.max_font_size.setValue(64)
    click("应用选中预设")
    assert window.max_font_size.value() == 32
    window.tabs.setCurrentIndex(0)
    click("编辑选中页")
    editor = window.page_editor
    editor.select_region("dialogue")
    automatic = redraw("01-auto-font")
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert saved["render_settings"]["max_font_size"] == 32
    assert 18 <= saved["regions"][0]["metadata"]["rendered_font_size"] <= 32
    report["checks"]["global_font_and_preset"] = True

    editor.font_size.setValue(44)
    editor.outline_width.setValue(0)
    editor.alignment.setCurrentIndex(editor.alignment.findData("left"))
    with patch.object(QColorDialog, "getColor", return_value=QColor("#d00000")):
        click("选择颜色")
    red = redraw("02-red-left-44")
    assert np.count_nonzero((red[:, :, 0] > 140) & (red[:, :, 1] < 60) & (red[:, :, 2] < 60)) > 100
    editor.font_size.setValue(28)
    editor.outline_width.setValue(3)
    editor.alignment.setCurrentIndex(editor.alignment.findData("right"))
    editor.rotation_value.setValue(15)
    with patch.object(QColorDialog, "getColor", return_value=QColor("#0050d0")):
        click("选择颜色")
    styled = redraw("03-blue-right-rotated-outline")
    assert not np.array_equal(styled, red)
    assert np.array_equal(styled, redraw("04-identical-rerender"))
    report["checks"]["styles_and_repeatable_rerender"] = True
    editor.direction.setCurrentIndex(editor.direction.findData("vertical"))
    vertical = redraw("04b-vertical")
    assert not np.array_equal(styled, vertical)
    editor.direction.setCurrentIndex(editor.direction.findData("horizontal"))
    report["checks"]["vertical_layout"] = True

    editor.preview_mode.setCurrentIndex(0)
    editor.mask_radius.setValue(24)
    click("蒙版画笔")
    for x, y in ((760, 475), (125, 110)):
        QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=editor.view.mapFromScene(x, y))
    click("结束绘制")
    click("保存编辑状态")
    controller._open_editor_metadata(metadata)
    assert editor._mask.pixelColor(760, 475).red() == 255
    editor.select_region("dialogue")
    masked = redraw("05-manual-mask")
    assert masked[469:482, 754:767].mean() > 245
    assert np.array_equal(masked[75:145, 70:185], original_pixels[75:145, 70:185])
    report["checks"]["mask_reopened_and_review_region_protected"] = True
    editor.select_region("effect")
    click("确认处理")
    accepted = redraw("06-effect-accepted")
    assert not np.array_equal(accepted[75:145, 70:185], original_pixels[75:145, 70:185])
    click("保留原文")
    rejected = redraw("07-effect-original-restored")
    assert np.array_equal(rejected[75:145, 70:185], original_pixels[75:145, 70:185])
    report["checks"]["explicit_accept_and_preserve_restore_original"] = True
    editor.findChild(QScrollArea).verticalScrollBar().setValue(0)
    editor.preview_mode.setCurrentIndex(0)
    qt.processEvents()
    window.grab().save(str(root / "ui-before.png"))
    editor.preview_mode.setCurrentIndex(1)
    qt.processEvents()
    window.grab().save(str(root / "ui-after.png"))
    assert not report["errors"], report["errors"]
    report["total_tokens"] = sum(call["usage"]["total_tokens"] for call in report["api_calls"])
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    window._allow_quit = True
    window.close()
    app.database.close()
    qt.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
