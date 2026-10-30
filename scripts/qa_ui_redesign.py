"""Capture real Qt screens at several sizes using isolated, generated fixtures.

No system credentials, paid API, existing task database or user manga is used.
Run --scale-factor 1.25 in a separate invocation to exercise Qt's high-DPI scaling.
The screenshots are actual QWidget.grab() output, not composited mockups.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scale-factor", type=float, default=1.0)
    args = parser.parse_args()
    if args.scale_factor not in (1.0, 1.25, 1.5):
        parser.error("--scale-factor must be 1, 1.25 or 1.5")
    root = args.output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Use a new or empty output directory to preserve previous evidence")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["QT_SCALE_FACTOR"] = str(args.scale_factor)

    import cv2
    import httpx
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
    from unittest.mock import patch

    from app.domain.enums import PageStatus, TaskStatus
    from app.domain.models import ApiChannelRecord, TextRegion
    from app.inpainting.opencv_inpainter import OpenCVInpainter
    from app.paths import AppPaths
    from app.rendering.pillow_renderer import PillowTextRenderer
    from app.security.credentials import CredentialStore
    from app.services.application import TranslationApplication
    from app.ui.controller import MainController
    from app.ui.main_window import MainWindow

    class EmptyCredentials:
        def get_password(self, *_args):
            return None

    async def forbid_network(*_args, **_kwargs):
        raise AssertionError("UI-only QA must not make any network request")

    report: dict = {"mode": "UI fixtures, real native Qt screenshots", "paid_api_requests": 0,
                    "system_credentials_used": False, "qt_scale_factor": args.scale_factor,
                    "screens": [], "errors": [], "fixture_records": True}
    paths = AppPaths(root / "runtime", root / "results", root / "temp")
    app = TranslationApplication(paths, credentials=CredentialStore(EmptyCredentials()))
    app.initialize()
    source_dir = root / "samples"
    source_dir.mkdir()
    original_dir = paths.output_root / "original"
    inpainted_dir = paths.output_root / "inpainted"
    translated_dir = paths.output_root / "translated"
    metadata_dir = paths.output_root / "metadata"
    for directory in [original_dir, inpainted_dir, translated_dir, metadata_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34)
    source = Image.new("RGB", (800, 1100), "#fffefa")
    draw = ImageDraw.Draw(source)
    for panel in [(35, 35, 765, 535), (35, 560, 765, 1065)]:
        draw.rectangle(panel, fill="white", outline="#343434", width=4)
    draw.ellipse((80, 75, 590, 250), outline="#343434", width=3)
    draw.text((335, 160), "GOOD MORNING!", fill="#222222", anchor="mm", font=font)
    draw.ellipse((250, 295, 450, 490), fill="#f4f4f0", outline="#343434", width=4)
    draw.arc((240, 275, 455, 430), 180, 360, fill="#343434", width=16)
    draw.ellipse((300, 365, 309, 378), fill="#343434")
    draw.ellipse((385, 365, 394, 378), fill="#343434")
    draw.arc((325, 390, 370, 430), 10, 170, fill="#343434", width=3)
    draw.line((260, 490, 215, 535), fill="#343434", width=4)
    draw.line((450, 490, 500, 535), fill="#343434", width=4)
    for shift in [0, 55, 110]:
        draw.line((65 + shift, 620, 155 + shift, 740), fill="#b8b8b0", width=2)
    draw.ellipse((190, 650, 700, 855), outline="#343434", width=3)
    draw.text((445, 750), "LET'S START!", fill="#222222", anchor="mm", font=font)
    draw.text((80, 950), "HEY!", fill="#444444", font=font)
    source_path = source_dir / "001.png"
    source.save(source_path)
    source.save(original_dir / "001.png")
    original_array = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
    regions = [
        TextRegion("dialogue-1", [(160, 115), (510, 115), (510, 205), (160, 205)],
                   "GOOD MORNING!", "早上好！", .99, metadata={"font_size": 38}),
        TextRegion("dialogue-2", [(270, 700), (625, 700), (625, 800), (270, 800)],
                   "LET'S START!", "开始今天的冒险吧！", .98, metadata={"font_size": 28}),
        TextRegion("review-3", [(65, 930), (190, 930), (190, 1000), (65, 1000)],
                   "HEY!", "嘿！", .51, needs_review=True),
    ]
    for region in regions:
        region.metadata["source_polygon"] = [list(point) for point in region.polygon]
    async def render_fixture():
        approved = [region for region in regions if region.approved]
        clean = await OpenCVInpainter().inpaint(original_array, approved)
        rendered = await PillowTextRenderer().render(clean, approved)
        return clean, rendered
    clean, rendered = asyncio.run(render_fixture())
    for path, image in [(inpainted_dir / "001_inpainted.png", clean), (translated_dir / "001_translated.png", rendered)]:
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        path.write_bytes(encoded.tobytes())
    metadata_path = metadata_dir / "001.json"
    metadata_path.write_text(json.dumps({"schema_version": 1, "source_path": str(source_path),
        "source_language": "en", "target_language": "zh-Hans", "regions": [asdict(region) for region in regions]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    completed_task = app.database.create_task(str(source_dir), str(paths.output_root))
    completed_ids = []
    for index in range(3):
        page_id = app.database.add_page(completed_task, index, str(source_path if index == 0 else source_dir / f"{index+1:03}.png"))
        app.database.update_page_status(page_id, PageStatus.COMPLETED, stage="保存", progress=100)
        completed_ids.append(page_id)
    app.database.update_task_status(completed_task, TaskStatus.COMPLETED)
    paused_task = app.database.create_task(str(root / "待处理章节"), str(paths.output_root / "next-chapter"))
    for index in range(4):
        page_id = app.database.add_page(paused_task, index, f"{index+1:03}.png")
        status = [PageStatus.COMPLETED, PageStatus.FAILED, PageStatus.PENDING, PageStatus.PENDING][index]
        app.database.update_page_status(page_id, status, stage="等待恢复", progress=100 if index == 0 else 0,
            error="示例：文件暂不可用，可修复后重试" if index == 1 else None)
    app.database.update_task_status(paused_task, TaskStatus.PAUSED)
    app.database.upsert_api_channel(ApiChannelRecord("example-deepseek", "DeepSeek", True,
        "https://api.deepseek.com/v1", "deepseek-flash", 60, 1, 60, 2, 10, "priority", False, "示例通道，尚未测试"))

    qt = QApplication.instance() or QApplication(sys.argv[:1])
    window = MainWindow(paths)
    controller = MainController(app, window)
    window.show_error = lambda message: report["errors"].append(message)
    window.resize(1440, 900)
    window.show()
    qt.processEvents()
    # Windows may clamp the first shown window to the work area at high DPI.
    # Apply the requested test size again after the native window exists.
    window.resize(1440, 900)

    def capture(name):
        QTest.qWait(80)
        path = root / f"{name}.png"
        assert window.grab().save(str(path))
        report["screens"].append({"file": path.name, "page": window.tabs.tabText(window.tabs.currentIndex()),
            "logical_size": [window.width(), window.height()], "device_pixel_ratio": window.devicePixelRatioF(),
            "minimum_size_hint": [window.minimumSizeHint().width(), window.minimumSizeHint().height()]})

    capture("workspace-empty-1440x900")
    window.tabs.setCurrentWidget(window.page_editor)
    QTest.qWait(100)
    window.page_editor.load_page(inpainted_dir / "001_inpainted.png", metadata_path,
        original_image=original_array, original_available=True)
    window.page_editor.select_region("dialogue-1")
    controller._active_task_id = completed_task
    controller._active_page_ids = completed_ids
    window.source_edit.setText(str(source_dir))
    window.batch_name_edit.setText("第一章 · 示例")
    window.set_queue_pages([Path("001.png"), Path("002.png"), Path("003.png")])
    window.update_queue_page(0, "完成")
    window.update_queue_page(1, "完成")
    window.update_queue_page(2, "待检查", "示例：1个文本框需要人工确认")
    window.set_original_preview(source_path)
    window.set_result_preview(translated_dir / "001_translated.png")
    window.set_progress("完成 · 完成3/3页 · 待检查1处", 100)
    window.page_counter.setText("完成 3 / 3 页")
    window.glossary_editor.setPlainText("勇者\t勇者\n魔法使い\t魔法师\nエルナ\t艾尔娜")
    window.prompt_editor.setPlainText("将漫画对白翻译成自然、简洁的中文。保持人物语气，优先使用术语表中的译名。")
    window.replacement_editor.setPlainText("...\t……\n!\t！")
    window.preset_name.setText("日漫转简中")
    window.preset_list.addItem("日漫转简中 · 示例预设", "fixture-preset")
    window.append_log("[界面检查] 当前使用独立示例数据，未调用翻译服务。")
    window.append_log("[完成] 3页示例已载入；可在页面编辑器中检查待确认区域。")

    with patch.object(httpx.AsyncClient, "request", forbid_network):
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            capture(f"page-{index:02}-1440x900")
        for width, height in [(1366, 768), (1024, 700)]:
            window.resize(width, height)
            window.tabs.setCurrentIndex(0)
            capture(f"workspace-{width}x{height}")
            window.tabs.setCurrentWidget(window.page_editor)
            QTest.qWait(80)
            window.page_editor.view.fitInView(window.page_editor.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            capture(f"editor-{width}x{height}")
        for scroll in window.page_editor.findChildren(QScrollArea):
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("editor-inspector-bottom-1024x700")
        window.resize(1024, 700)
        window.tabs.setCurrentIndex(1)
        page = window.tabs.widget(1)
        for scroll in ([page] if isinstance(page, QScrollArea) else []) + page.findChildren(QScrollArea):
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        capture("api-bottom-1024x700")
        window.tabs.setCurrentIndex(0)
        window.stage_label.setText("当前阶段：正在安全退出，等待当前步骤结束；未完成页面下次可恢复")
        window.set_busy(True)
        capture("workspace-long-status-1024x700")
    report["api_key_field_empty"] = not bool(window.api_key.text())
    report["tab_count"] = window.tabs.count()
    report["qt_platform"] = qt.platformName()
    report["actual_network_requests"] = 0
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    window._allow_quit = True
    window.close()
    app.database.close()
    qt.processEvents()
    print(json.dumps({"screenshots": len(report["screens"]), "scale": args.scale_factor,
                      "errors": report["errors"], "output": str(root)}, ensure_ascii=False))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
