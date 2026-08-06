from app.paths import AppPaths
from app.ui.main_window import MainWindow


def test_main_window_uses_chinese_and_deepseek_defaults(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    assert window.windowTitle() == "个人漫画翻译器"
    assert window.tabs.tabText(0) == "开始翻译"
    assert window.tabs.tabText(1) == "API管理"
    assert window.api_service_name.text() == "DeepSeek"
    assert window.api_base_url.text() == "https://api.deepseek.com/v1"
    assert window.api_model.currentText() == "deepseek-v4-flash"
    assert window.api_key.echoMode().name == "Password"
    assert window.output_edit.text() == str(AppPaths.default().output_root)
    assert "多图" in window.batch_name_edit.placeholderText()


def test_progress_updates_stage_and_value(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    window.set_progress("OCR", 30)

    assert window.stage_label.text() == "当前阶段：OCR"
    assert window.progress_bar.value() == 30


def test_task_controls_are_enabled_only_while_busy(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    assert not window.pause_button.isEnabled()
    assert not window.resume_button.isEnabled()
    assert not window.cancel_button.isEnabled()

    window.set_busy(True)

    assert window.pause_button.isEnabled()
    assert window.resume_button.isEnabled()
    assert window.cancel_button.isEnabled()

    window.set_busy(False)

    assert not window.pause_button.isEnabled()
    assert not window.resume_button.isEnabled()
    assert not window.cancel_button.isEnabled()


def test_second_stage_tabs_and_optional_engines_are_visible(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    tab_names = [window.tabs.tabText(index) for index in range(window.tabs.count())]

    assert "任务历史" in tab_names
    assert "提示词与术语表" in tab_names
    assert "预设管理" in tab_names
    assert "可视化编辑器" in tab_names
    assert window.ocr_engine.findData("mangaocr") >= 0
    assert window.inpainting_engine.findData("lama_onnx") >= 0


def test_api_model_selector_accepts_fetched_choices_and_custom_text(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    window.set_api_model_choices(["deepseek-v4-flash", "deepseek-v4-pro"])
    window.api_model.setCurrentText("custom-model")

    assert window.api_model.isEditable()
    assert [window.api_model.itemText(index) for index in range(window.api_model.count())] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
    assert window.api_model.currentText() == "custom-model"


def test_high_accuracy_ocr_checkbox_applies_from_preset(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    assert not window.high_accuracy_ocr.isChecked()

    window.apply_preset_values({"high_accuracy_ocr": True})

    assert window.high_accuracy_ocr.isChecked()


def test_concurrent_pages_spinbox_defaults_and_applies_preset(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    assert window.concurrent_pages.value() == 1

    window.concurrent_pages.setValue(3)
    window.apply_preset_values({"concurrent_pages": 2})

    assert window.concurrent_pages.value() == 2


def test_main_window_close_hides_to_tray(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(
        QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
    )
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert not window.isVisible()
    window.show_from_tray()
    assert window.isVisible()


def test_tray_exit_action_requests_quit(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    exited: list[bool] = []
    window.exit_requested.connect(lambda: exited.append(True))

    exit_action = next(
        action
        for action in window.tray_icon.contextMenu().actions()
        if action.text() == "退出"
    )
    exit_action.trigger()

    assert exited == [True]
