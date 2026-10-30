from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from app.paths import AppPaths
from app.services.application import TranslationApplication
from app.ui.controller import MainController
from app.ui.main_window import MainWindow
from app.windows_identity import APPLICATION_NAME, ORGANIZATION_NAME, configure_notification_assets, set_process_app_id


def create_application(*, register_shell_identity: bool = False) -> tuple[QApplication, MainWindow, MainController]:
    # Set this on the real GUI process, not the short-lived VBS/Python bootstrap.
    set_process_app_id()
    qt_application = QApplication.instance() or QApplication(sys.argv)
    qt_application.setApplicationName(APPLICATION_NAME)
    qt_application.setApplicationDisplayName(APPLICATION_NAME)
    qt_application.setOrganizationName(ORGANIZATION_NAME)
    qt_application.setWindowIcon(MainWindow._app_icon())
    qt_application.setQuitOnLastWindowClosed(False)
    paths = AppPaths.default()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(paths.data_dir / "app.lock"))
    if not instance_lock.tryLock(0):
        raise RuntimeError("个人漫画翻译器已经在运行，请从任务栏或系统托盘打开已有窗口。")
    qt_application._instance_lock = instance_lock
    if register_shell_identity:
        configure_notification_assets(qt_application.windowIcon(), paths.project_root / ".cache" / "ui")
    application = TranslationApplication(paths)
    application.initialize()
    window = MainWindow(paths)
    controller = MainController(application, window)
    return qt_application, window, controller


def main() -> int:
    try:
        application, window, controller = create_application(register_shell_identity=True)
    except RuntimeError as error:
        QMessageBox.information(None, "个人漫画翻译器", str(error))
        return 1
    window.show()
    try:
        return application.exec()
    finally:
        controller.application.database.close()
