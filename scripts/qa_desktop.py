"""Exercise the real desktop widgets and local OCR with generated sample pages.

The default is offline. --live explicitly enables paid DeepSeek calls with generated
sample text. Reports never contain the API key; task data is isolated in --output-dir.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QPoint, Qt, QThread, QTimer
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QPushButton, QScrollArea

from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.ui.controller import MainController
from app.ui.main_window import MainWindow
from app.translation.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleTranslator


class EphemeralCredentials:
    def __init__(self, key: str | None = None) -> None:
        self.key = key

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.key if username == "deepseek-default" else None

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.key = password


def load_live_key(path: Path | None) -> str:
    if path is None:
        key = CredentialStore().load("deepseek-default")
    else:
        key = None
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*$", line)
            if match:
                key = match.group(1).strip().strip("\"'")
                break
    if not key:
        raise ValueError("No DeepSeek key found. Save it in the app or pass --key-file with a DEEPSEEK_API_KEY env file.")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Use paid DeepSeek translation for generated sample text")
    parser.add_argument("--key-file", type=Path, help="Read DEEPSEEK_API_KEY locally; requires --live")
    parser.add_argument("--model", help="An actual model ID returned by the account's model list")
    args = parser.parse_args()
    if (args.key_file or args.model) and not args.live:
        parser.error("--key-file and --model require --live")
    root = args.output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("--output-dir must be a new or empty directory; existing QA evidence is preserved")
    root.mkdir(parents=True, exist_ok=True)
    report: dict = {"mode": "live" if args.live else "offline", "tabs": [], "errors": [], "api_calls": []}
    screenshots = root / "screenshots"
    screenshots.mkdir(exist_ok=True)
    source_dir = root / "samples"
    source_dir.mkdir(exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
    for index, text in enumerate(("HELLO FRIEND!", "LET US GO HOME!"), start=1):
        image = Image.new("RGB", (900, 600), "#f3f4f6")
        draw = ImageDraw.Draw(image)
        draw.rectangle((25, 25, 875, 575), fill="white", outline="black", width=4)
        draw.ellipse((100, 130, 800, 420), fill="white", outline="black", width=3)
        draw.text((450, 270), text, font=font, anchor="mm", fill="black")
        image.save(source_dir / f"{index:03}.png")

    qt = QApplication.instance() or QApplication(sys.argv[:1])
    paths = AppPaths(root / "runtime", root / "results", root / "temp")
    key = load_live_key(args.key_file) if args.live else None
    application = TranslationApplication(paths, credentials=CredentialStore(EphemeralCredentials(key)))
    application.initialize()
    request_patcher = None
    if args.live:
        original_request = OpenAICompatibleTranslator._request

        async def observe_request(translator, payload):
            # This opt-in QA uses only a few short bubbles, with a bounded response.
            payload = {**payload, "max_tokens": 1024}
            started = time.monotonic()
            response = await original_request(translator, payload)
            body = response.json()
            report["api_calls"].append({
                "model": body.get("model"), "http_status": response.status_code,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": body.get("usage", {}),
                "finish_reason": body.get("choices", [{}])[0].get("finish_reason"),
            })
            return response

        request_patcher = patch.object(OpenAICompatibleTranslator, "_request", observe_request)
        request_patcher.start()

        async def configure_live():
            adapter = OpenAICompatibleTranslator(OpenAICompatibleConfig.deepseek(key))
            try:
                models = await adapter.list_models()
            finally:
                await adapter.close()
            report["available_models"] = models
            model = args.model or ("deepseek-flash" if "deepseek-flash" in models else models[0])
            if model not in models:
                raise ValueError("The requested model is not available to this account")
            report["model"] = model
            application.save_api_channel({
                "id": "deepseek-default", "service_name": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1", "model": model,
                "api_key": key, "enabled": True, "retries": 0,
            })
            await application.test_api_channel("deepseek-default")
            japanese = ["おはよう、今日は一緒に学校へ行こう。", "待って！その扉を開けないで！", "ありがとう。また明日会おうね。"]
            adapter = OpenAICompatibleTranslator(OpenAICompatibleConfig(
                service_name="DeepSeek", api_key=key, base_url="https://api.deepseek.com/v1",
                model=model, retries=0,
            ))
            try:
                translated = await adapter.translate(japanese, "ja", "zh-Hans")
                report["japanese_dialogue"] = [{"source": a, "translation": b} for a, b in zip(japanese, translated, strict=True)]
            finally:
                await adapter.close()

        asyncio.run(configure_live())
    window = MainWindow(paths)
    controller = MainController(application, window)
    window.show()
    QTest.qWait(200)

    def click_button(label: str) -> None:
        buttons = [b for b in window.findChildren(QPushButton) if b.text() == label]
        if not buttons:
            actions = [a for a in window.findChildren(QAction) if a.text() == label]
            if len(actions) != 1:
                raise AssertionError(f"Expected one button/menu action {label!r}, got {len(actions)}")
            action = actions[0]
            menus = [obj for obj in action.associatedObjects() if isinstance(obj, QMenu)]
            if len(menus) != 1:
                raise AssertionError(f"Menu action {label!r} has no unique parent menu")
            menu = menus[0]
            owners = [b for b in window.findChildren(QPushButton) if b.menu() is menu]
            if len(owners) != 1:
                raise AssertionError(f"Menu action {label!r} has no unique menu button")

            def choose_action() -> None:
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())

            # QPushButton opens the native Qt menu in a nested event loop.
            # Schedule the real menu click before opening it.
            QTimer.singleShot(50, choose_action)
            QTest.mouseClick(owners[0], Qt.MouseButton.LeftButton)
            QTest.qWait(100)
            qt.processEvents()
            return
        if len(buttons) != 1:
            raise AssertionError(f"Expected one button {label!r}, got {len(buttons)}")
        button = buttons[0]
        if window.tabs.currentWidget() is window.page_editor:
            window.page_editor.findChild(QScrollArea).ensureWidgetVisible(button)
            qt.processEvents()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        qt.processEvents()

    def wait_for_worker() -> None:
        deadline = time.monotonic() + 120
        while (controller._queue_running or controller._threads) and time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.02)
        if controller._threads:
            raise RuntimeError("Desktop QA timed out while the worker was still running")

    def capture_error(message: str) -> None:
        report["errors"].append(message)

    # Errors are recorded instead of leaving this unattended QA run in a modal loop.
    window.show_error = capture_error
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        QTest.qWait(50)
        name = window.tabs.tabText(index)
        report["tabs"].append(name)
        window.grab().save(str(screenshots / f"tab-{index:02}.png"))
    window.tabs.setCurrentIndex(0)
    window.source_edit.setText(str(source_dir))
    batch_name = "live-demo" if args.live else "offline-demo"
    window.batch_name_edit.setText(batch_name)
    if not args.live:
        # The redesigned checkbox occupies a full layout row; its blank right
        # side is outside the native checkbox hit area. Click the indicator.
        QTest.mouseClick(window.translate_checkbox, Qt.MouseButton.LeftButton,
                         pos=QPoint(8, window.translate_checkbox.height() // 2))
        assert not window.translate_checkbox.isChecked()
    QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    wait_for_worker()
    window.grab().save(str(screenshots / "batch-completed.png"))
    tasks = application.database.list_tasks()
    report["tasks"] = [{"status": t.status.value, "output": t.output_dir} for t in tasks]
    metadata = sorted((root / "results" / batch_name / "metadata").glob("*.json"))
    pages = []
    for path in metadata:
        document = json.loads(path.read_text(encoding="utf-8"))
        original = source_dir / f"{path.stem}.png"
        result = root / "results" / batch_name / "translated" / f"{path.stem}_translated.png"
        same_pixels = Image.open(original).convert("RGB").tobytes() == Image.open(result).convert("RGB").tobytes()
        pages.append({"page": path.stem, "texts": [r["source_text"] for r in document["regions"]], "translations": [r["translated_text"] for r in document["regions"]], "same_pixels_as_original": same_pixels})
        if args.live:
            assert any(r["translated_text"].strip() for r in document["regions"])
            shutil.copyfile(result, root / f"api-translated-{path.stem}.png")
    report["pages"] = pages
    if args.live and pages:
        calls_before = len(report["api_calls"])
        cached = application.build_translator()
        cached_text = asyncio.run(cached.translate(pages[0]["texts"], "auto", "zh-Hans"))
        report["cache_hit_without_api_request"] = len(report["api_calls"]) == calls_before
        assert report["cache_hit_without_api_request"] and cached_text == pages[0]["translations"]
    if metadata:
        window.queue_table.selectRow(0)
        click_button("编辑选中页")
        editor = window.page_editor
        if not editor.region_count:
            raise AssertionError("The selected page did not load into the editor")
        region_id = next(iter(editor._regions))
        point = editor.view.mapFromScene(editor._items[region_id].sceneBoundingRect().center())
        QTest.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        editor.translation_text.setText("你好，朋友！")
        show_preview = editor.show_rendered_preview

        def inspect_preview(path):
            report["render_callback_on_gui_thread"] = QThread.currentThread() == qt.thread()
            show_preview(path)

        editor.show_rendered_preview = inspect_preview
        click_button("保存并重绘译图")
        wait_for_worker()
        assert report["render_callback_on_gui_thread"] is True
        editor.findChild(QScrollArea).verticalScrollBar().setValue(0)
        qt.processEvents()
        editor.view.viewport().repaint()
        time.sleep(0.1)
        qt.processEvents()
        window.grab().save(str(screenshots / "editor-loaded.png"))
        saved = json.loads(metadata[0].read_text(encoding="utf-8"))
        report["editor_saved_translation"] = saved["regions"][0]["translated_text"]
        assert report["editor_saved_translation"] == "你好，朋友！"
        window.tabs.setCurrentIndex(0)
        window.grab().save(str(screenshots / "manual-render-completed.png"))
        # Native OS file pickers are stubbed; real UI actions and file writers run.
        for label, path in (("导出TXT", root / "ocr-text.txt"), ("导出JSON", root / "ocr-text.json"), ("导出译图ZIP", root / "preview.zip")):
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                click_button(label)
            wait_for_worker()
            assert path.is_file() and path.stat().st_size > 0
        with zipfile.ZipFile(root / "preview.zip") as archive:
            assert archive.testzip() is None
            report["exported_zip_entries"] = archive.namelist()
        report["native_file_dialogs"] = "stubbed; UI actions, OCR, editing, rendering and exports are real"
    if not args.live:
        assert all(page["same_pixels_as_original"] for page in pages)
    report["total_tokens"] = sum(call["usage"].get("total_tokens", 0) for call in report["api_calls"])
    report["stage"] = window.stage_label.text()
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    window._allow_quit = True
    window.close()
    application.database.close()
    qt.processEvents()
    if request_patcher is not None:
        request_patcher.stop()
    return 0 if metadata and not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
