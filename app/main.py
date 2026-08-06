from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.paths import AppPaths
from app.services.application import TranslationApplication
from app.ui.controller import MainController
from app.ui.main_window import MainWindow


def create_application() -> tuple[QApplication, MainWindow, MainController]:
    qt_application = QApplication.instance() or QApplication(sys.argv)
    qt_application.setApplicationName("个人漫画翻译器")
    qt_application.setOrganizationName("Personal")
    paths = AppPaths.default()
    application = TranslationApplication(paths)
    application.initialize()
    window = MainWindow(paths)
    controller = MainController(application, window)
    return qt_application, window, controller


def main() -> int:
    application, window, controller = create_application()
    window.show()
    try:
        return application.exec()
    finally:
        controller.application.database.close()
