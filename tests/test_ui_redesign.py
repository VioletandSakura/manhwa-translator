"""Workflow and readability checks for the redesigned native desktop shell."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QPushButton, QScrollArea

from app.paths import AppPaths
from app.ui.main_window import MainWindow


PAGE_NAMES = [
    "开始翻译", "API管理", "OCR设置", "检测与擦字设置", "渲染与字体",
    "提示词与术语表", "替换规则", "预设管理", "任务历史", "日志与诊断", "可视化编辑器",
]


def _window(qtbot, tmp_path):
    window = MainWindow(AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp"))
    qtbot.addWidget(window)
    window.show()
    return window


def _window_rect(widget, window):
    return QRect(widget.mapTo(window, QPoint()), widget.size())


def test_sidebar_and_original_page_api_stay_synchronized(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == PAGE_NAMES
    assert window.tabs.widget(10) is window.page_editor
    assert not window.tabs.tabBar().isVisible()
    assert set(window.nav_buttons) == set(range(11))
    for index in [8, 1, 10, 0, 5]:
        QTest.mouseClick(window.nav_buttons[index], Qt.MouseButton.LeftButton)
        assert window.tabs.currentIndex() == index
        assert window.nav_buttons[index].isChecked()
        assert sum(button.isChecked() for button in window.nav_buttons.values()) == 1
    window.tabs.setCurrentWidget(window.page_editor)
    assert window.nav_buttons[10].isChecked()


@pytest.mark.parametrize("width,height", [(1024, 700), (1366, 768), (1440, 900)])
def test_compact_workspace_keeps_task_controls_inside_window(qtbot, tmp_path, width, height):
    window = _window(qtbot, tmp_path)
    window.resize(width, height)
    QTest.qWait(40)
    assert window.width() == width and window.height() == height
    assert window.minimumSizeHint().width() <= width
    assert window.minimumSizeHint().height() <= height
    bounds = window.rect()
    for widget in [window.stage_label, window.progress_bar, window.start_button,
                   window.pause_button, window.resume_button, window.cancel_button]:
        for scroll in window.findChildren(QScrollArea):
            if scroll.widget() is not None and scroll.widget().isAncestorOf(widget):
                scroll.ensureWidgetVisible(widget)
        QTest.qWait(5)
        assert widget.isVisible(), widget.objectName() or widget.text()
        assert bounds.contains(_window_rect(widget, window)), widget.objectName() or widget.text()
    for index, button in window.nav_buttons.items():
        assert bounds.contains(_window_rect(button, window)), PAGE_NAMES[index]
    # A child can fit inside the overall window while being cropped by its
    # narrower scroll viewport, especially after combo-arrow styling changes.
    input_scroll = next(scroll for scroll in window.findChildren(QScrollArea)
                        if scroll.widget() is not None and scroll.widget().isAncestorOf(window.source_edit))
    assert input_scroll.horizontalScrollBar().maximum() == 0
    for widget in [window.source_edit, window.output_edit, window.source_language, window.target_language]:
        input_scroll.ensureWidgetVisible(widget)
        QTest.qWait(5)
        rectangle = QRect(widget.mapTo(input_scroll.viewport(), QPoint()), widget.size())
        assert input_scroll.viewport().rect().contains(rectangle), widget.accessibleName()


def test_form_pages_allow_reaching_bottom_controls_in_small_window(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window.resize(1024, 700)
    for index in [1, 2, 3, 4, 5, 6, 7]:
        window.tabs.setCurrentIndex(index)
        QTest.qWait(20)
        page = window.tabs.widget(index)
        scrolls = ([page] if isinstance(page, QScrollArea) else []) + page.findChildren(QScrollArea)
        assert scrolls, PAGE_NAMES[index]
        for scroll in scrolls:
            assert scroll.widgetResizable()
            assert scroll.horizontalScrollBar().maximum() == 0, PAGE_NAMES[index]
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            QTest.qWait(5)
            assert scroll.viewport().width() > 200


def test_output_tables_are_readonly_and_select_whole_rows(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    for table in [window.queue_table, window.api_table, window.history_table]:
        assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    window.set_queue_pages([Path("第1页.png"), Path("第2页.png")])
    window.queue_table.selectRow(1)
    assert window.queue_table.currentRow() == 1
    assert len(window.queue_table.selectedItems()) == 4


def test_core_inputs_and_navigation_are_keyboard_accessible(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    for widget in [window.source_edit, window.output_edit, window.source_language,
                   window.target_language, window.concurrent_pages]:
        assert widget.accessibleName().strip(), widget.objectName()
        assert widget.focusPolicy() != Qt.FocusPolicy.NoFocus
    window.source_edit.setFocus()
    QTest.keyClicks(window.source_edit, "sample.png")
    assert window.selected_sources() == [Path("sample.png")]
    button = window.nav_buttons[8]
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert window.tabs.currentIndex() == 8


def test_previews_rescale_after_window_resize_without_distorting_page(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    image_path = tmp_path / "portrait.png"
    source = QPixmap(400, 600)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(image_path))
    window.resize(1440, 900)
    QTest.qWait(30)
    window.set_original_preview(image_path)
    QTest.qWait(30)
    large = window.original_preview.pixmap().size()
    window.resize(1024, 700)
    QTest.qWait(40)
    label = window.original_preview
    small = label.pixmap().size()
    assert small.width() <= label.contentsRect().width()
    assert small.height() <= label.contentsRect().height()
    assert abs(small.width() / small.height() - 2 / 3) < 0.02
    assert small != large


def test_busy_state_preserves_control_and_export_safety(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window.set_busy(True)
    assert not window.start_button.isEnabled()
    assert all(not button.isEnabled() for button in window._task_action_buttons)
    assert window.pause_button.isEnabled() and window.resume_button.isEnabled() and window.cancel_button.isEnabled()
    window.set_progress("翻译 · 完成2/3页 · 失败0页", 66)
    assert window.progress_bar.value() == 66
    assert "完成2/3页" in window.stage_label.text()
    window.set_busy(False)
    assert window.start_button.isEnabled()


def test_saving_preset_selects_new_item_despite_placeholder_and_restores_font(qtbot, tmp_path):
    import json
    from app.security.credentials import CredentialStore
    from app.services.application import TranslationApplication
    from app.ui.controller import MainController

    class EmptyCredentials:
        def get_password(self, *_args):
            return None

    paths = AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp")
    application = TranslationApplication(paths, credentials=CredentialStore(EmptyCredentials()))
    application.initialize()
    application.save_library_content("preset", "A旧预设", json.dumps({"render_settings": {"min_font_size": 10, "max_font_size": 80}}))
    window = MainWindow(paths)
    qtbot.addWidget(window)
    controller = MainController(application, window)
    window.show()
    window.tabs.setCurrentIndex(7)
    QTest.qWait(25)
    assert window.preset_list.placeholderText()
    window.preset_name.setText("Z新预设")
    window.min_font_size.setValue(12)
    window.max_font_size.setValue(32)

    def click(text):
        button = next(item for item in window.findChildren(QPushButton) if item.text() == text)
        page = window.tabs.currentWidget()
        for scroll in ([page] if isinstance(page, QScrollArea) else []) + page.findChildren(QScrollArea):
            if scroll.widget().isAncestorOf(button):
                scroll.ensureWidgetVisible(button)
        QTest.qWait(15)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    click("保存当前设置为预设")
    assert window.preset_list.currentText() == "Z新预设"
    assert window.preset_list.currentData()
    window.max_font_size.setValue(64)
    click("应用选中预设")
    assert window.min_font_size.value() == 12
    assert window.max_font_size.value() == 32
    application.database.close()
