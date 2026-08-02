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
    assert window.api_model.text() == "deepseek-chat"
    assert window.api_key.echoMode().name == "Password"
    assert window.output_edit.text() == str(AppPaths.default().output_root)


def test_progress_updates_stage_and_value(qtbot) -> None:
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)

    window.set_progress("OCR", 30)

    assert window.stage_label.text() == "当前阶段：OCR"
    assert window.progress_bar.value() == 30

