"""Windows shell identity, independent of the interpreter/launcher filename.

The process ID is set before QApplication. Persistent notification assets are
registered only by the normal application entry point, never merely by importing
this module or creating an application in an embedding host/test.

References:
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid
https://github.com/microsoft/WindowsAppSDK/blob/main/dev/AppNotifications/AppNotificationUtility.cpp
"""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import sys


APPLICATION_NAME = "个人漫画翻译器"
ORGANIZATION_NAME = "Personal"
# Stable across upgrades and independent of where the source/venv is installed.
APP_USER_MODEL_ID = "Personal.MangaTranslator"
_LOGGER = logging.getLogger(__name__)


def _shell32():
    return ctypes.WinDLL("shell32", use_last_error=True)


def set_process_app_id() -> bool:
    """Give the actual GUI process its identity before creating any Qt UI."""
    if sys.platform != "win32":
        return False
    try:
        setter = _shell32().SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(APP_USER_MODEL_ID)
        if result != 0:
            _LOGGER.warning("Windows应用身份设置失败（HRESULT 0x%08X），继续启动。", result & 0xFFFFFFFF)
            return False
        return True
    except (AttributeError, OSError) as error:
        _LOGGER.warning("Windows应用身份暂不可用，继续启动：%s", error)
        return False


def save_notification_icon(icon, target: Path) -> bool:
    """Export the app's existing artwork, atomically and only when it changes."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from app.storage.atomic import write_bytes

    try:
        content = QByteArray()
        buffer = QBuffer(content)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError("无法准备通知图标")
        if not icon.pixmap(64, 64).save(buffer, "ICO"):
            raise OSError("无法编码Windows通知图标")
        buffer.close()
        data = bytes(content)
        target = Path(target)
        if target.is_file() and target.read_bytes() == data:
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        write_bytes(target, data)
        return True
    except (OSError, RuntimeError) as error:
        _LOGGER.warning("Windows通知图标暂不可用，继续启动：%s", error)
        return False


def register_notification_assets(icon_path: Path) -> bool:
    """Register only this app's friendly name/icon in the current user's hive.

    This is asset metadata for the existing QSystemTrayIcon notifications, not a
    COM activator or permission change. In particular, do not write notification
    Enabled/ShowBanner values or change another application's registration.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        icon_path = Path(icon_path).resolve()
        if not icon_path.is_file():
            raise OSError("通知图标文件不存在")
        subkey = rf"Software\Classes\AppUserModelId\{APP_USER_MODEL_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_EXPAND_SZ, APPLICATION_NAME)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_EXPAND_SZ, str(icon_path))
        return True
    except (ImportError, OSError) as error:
        _LOGGER.warning("Windows通知名称注册暂不可用，继续启动：%s", error)
        return False


def configure_notification_assets(icon, directory: Path) -> bool:
    if sys.platform != "win32":
        return False
    target = Path(directory) / "application.ico"
    return save_notification_icon(icon, target) and register_notification_assets(target)
