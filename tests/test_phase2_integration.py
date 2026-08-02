from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from app.domain.enums import TaskStatus
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.storage.database import Database
from app.ui.controller import MainController
from app.ui.main_window import MainWindow

from test_application_services import MemoryCredentialBackend


def _application(tmp_path: Path) -> TranslationApplication:
    paths = AppPaths(tmp_path / "project", tmp_path / "results", tmp_path / "temp")
    application = TranslationApplication(
        paths,
        database=Database(paths.database_path),
        credentials=CredentialStore(MemoryCredentialBackend()),
    )
    application.initialize()
    return application


def test_controller_processes_folder_as_persistent_offline_batch(tmp_path: Path, qtbot, monkeypatch) -> None:
    application = _application(tmp_path)
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    Image.new("RGB", (160, 120), "white").save(chapter / "2.png")
    Image.new("RGB", (160, 120), "white").save(chapter / "1.png")
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window._selected_sources = [chapter]
    window.source_edit.setText(str(chapter))
    window.output_edit.setText(str(tmp_path / "out"))
    window.batch_name_edit.setText("测试漫画")
    window.translate_checkbox.setChecked(False)
    captured = {}

    def capture_worker(operation, *, on_completed, on_failed):
        captured["operation"] = operation
        captured["completed"] = on_completed

    monkeypatch.setattr(controller, "_start_worker", capture_worker)
    controller.start_translation()
    task_id = asyncio.run(captured["operation"](window.set_progress))
    captured["completed"](task_id)

    assert application.database.get_task(task_id).status is TaskStatus.COMPLETED
    assert window.queue_table.rowCount() == 2
    task = application.database.get_task(task_id)
    assert Path(task.output_dir) == tmp_path / "out" / "测试漫画"
    batch_output = tmp_path / "out" / "测试漫画"
    assert len(list((batch_output / "original").glob("*.png"))) == 2
    assert len(list((batch_output / "inpainted").glob("*.png"))) == 2
    assert len(list((batch_output / "translated").glob("*.png"))) == 2
    assert len(list((batch_output / "metadata").glob("*.json"))) == 2

    controller.resume_batch()

    assert application.database.get_task(task_id).status is TaskStatus.COMPLETED
    assert window.stage_label.text().endswith("完成")
    assert not window.resume_button.isEnabled()


def test_controller_requires_batch_name_before_creating_folder_task(tmp_path: Path, qtbot, monkeypatch) -> None:
    application = _application(tmp_path)
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    Image.new("RGB", (40, 40), "white").save(chapter / "1.png")
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window._selected_sources = [chapter]
    window.source_edit.setText(str(chapter))
    window.output_edit.setText(str(tmp_path / "out"))
    errors: list[str] = []
    monkeypatch.setattr(window, "show_error", errors.append)

    controller.start_translation()

    assert errors == ["多图、文件夹、ZIP 或 EPUB 任务必须填写批次名称。"]
    assert application.database.list_tasks() == []


def test_controller_saves_local_content_preset_and_api_channel(tmp_path: Path, qtbot) -> None:
    application = _application(tmp_path)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.glossary_editor.setPlainText("勇者\t勇者")
    window.prompt_editor.setPlainText("忠实翻译")
    window.replacement_editor.setPlainText("...\t……")
    window.preset_name.setText("日漫简中")
    window.api_key.setText("test-local-key")

    controller.save_library_content()
    controller.save_replacement_rules()
    controller.save_preset()
    controller.save_api_config()

    assert application.list_library_content("glossary")[0].payload == "勇者\t勇者"
    assert application.list_library_content("preset")[0].name == "日漫简中"
    assert application.database.get_api_channel("deepseek-default").service_name == "DeepSeek"


def test_controller_applies_saved_preset_without_api_key(tmp_path: Path, qtbot) -> None:
    application = _application(tmp_path)
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.preset_name.setText("日漫简中")
    window.ocr_engine.setCurrentIndex(window.ocr_engine.findData("mangaocr"))
    window.inpainting_engine.setCurrentIndex(window.inpainting_engine.findData("lama_onnx"))
    window.api_key.setText("must-not-enter-preset")
    controller.save_preset()

    window.ocr_engine.setCurrentIndex(window.ocr_engine.findData("rapidocr"))
    window.inpainting_engine.setCurrentIndex(window.inpainting_engine.findData("opencv"))
    controller.apply_preset()

    assert window.ocr_engine.currentData() == "mangaocr"
    assert window.inpainting_engine.currentData() == "lama_onnx"
    preset = application.list_library_content("preset")[0]
    assert "must-not-enter-preset" not in preset.payload


def test_controller_resumes_selected_persistent_history_task(tmp_path: Path, qtbot, monkeypatch) -> None:
    application = _application(tmp_path)
    source = tmp_path / "page.png"
    Image.new("RGB", (120, 80), "white").save(source)
    batch = application.create_batch_task([source], tmp_path / "out")
    window = MainWindow(application.paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.translate_checkbox.setChecked(False)
    window.set_history(application.database.list_tasks())
    window.history_table.selectRow(0)
    captured = {}

    def capture_worker(operation, *, on_completed, on_failed):
        captured["operation"] = operation
        captured["completed"] = on_completed

    monkeypatch.setattr(controller, "_start_worker", capture_worker)
    controller.resume_history_task()
    task_id = asyncio.run(captured["operation"](window.set_progress))
    captured["completed"](task_id)

    assert task_id == batch.task_id
    assert application.database.get_task(task_id).status is TaskStatus.COMPLETED
    assert window.queue_table.rowCount() == 1


def test_application_rerenders_modified_metadata_without_api_call(tmp_path: Path) -> None:
    application = _application(tmp_path)
    output = tmp_path / "out"
    (output / "inpainted").mkdir(parents=True)
    (output / "metadata").mkdir()
    Image.new("RGB", (120, 80), "white").save(output / "inpainted" / "page_inpainted.png")
    metadata = output / "metadata" / "page.json"
    metadata.write_text(
        '{"regions":[{"id":"r1","polygon":[[10,10],[100,10],[100,60],[10,60]],"translated_text":"你好"}]}',
        encoding="utf-8",
    )

    translated = asyncio.run(application.rerender_metadata(metadata))

    assert translated.is_file()
