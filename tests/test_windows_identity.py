from __future__ import annotations

from contextlib import nullcontext
import ctypes
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QSystemTrayIcon

from app.paths import AppPaths
from app.ui.main_window import MainWindow
from app import windows_identity as identity


def test_process_identity_uses_wide_unicode_windows_api(monkeypatch):
    values = []
    class Setter:
        def __call__(self, value):
            values.append(value)
            return 0
    setter = Setter()
    monkeypatch.setattr(identity.sys, "platform", "win32")
    monkeypatch.setattr(identity, "_shell32", lambda: SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=setter))
    assert identity.set_process_app_id()
    assert values == [identity.APP_USER_MODEL_ID]
    assert setter.argtypes == [ctypes.c_wchar_p]
    assert setter.restype is ctypes.c_long
    assert len(values[0]) <= 128 and " " not in values[0]


@pytest.mark.parametrize("result", [-2147467259, 2147500037])
def test_native_identity_hresult_failure_is_nonfatal(monkeypatch, caplog, result):
    class Setter:
        def __call__(self, value):
            return result
    monkeypatch.setattr(identity.sys, "platform", "win32")
    monkeypatch.setattr(identity, "_shell32", lambda: SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=Setter()))
    assert not identity.set_process_app_id()
    assert "0x80004005" in caplog.text


def test_non_windows_hosts_do_not_load_shell_or_register_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(identity.sys, "platform", "linux")
    monkeypatch.setattr(identity, "_shell32", lambda: pytest.fail("Windows API must not load on another OS"))
    assert not identity.set_process_app_id()
    assert not identity.configure_notification_assets(None, tmp_path)
    assert not identity.register_notification_assets(tmp_path / "missing.ico")
    assert list(tmp_path.iterdir()) == []


def test_native_api_load_failure_does_not_prevent_startup(monkeypatch, caplog):
    monkeypatch.setattr(identity.sys, "platform", "win32")
    def unavailable():
        raise OSError("unsupported shell")
    monkeypatch.setattr(identity, "_shell32", unavailable)
    assert not identity.set_process_app_id()
    assert "继续启动" in caplog.text


def test_notification_icon_is_valid_and_unchanged_artwork_is_not_rewritten(qapp, tmp_path):
    target = tmp_path / "identity" / "application.ico"
    icon = MainWindow._app_icon()
    assert identity.save_notification_icon(icon, target)
    first = target.read_bytes()
    first_time = target.stat().st_mtime_ns
    assert not QImage(str(target)).isNull()
    assert identity.save_notification_icon(icon, target)
    assert target.read_bytes() == first
    assert target.stat().st_mtime_ns == first_time
    target.write_bytes(b"corrupted icon")
    assert identity.save_notification_icon(icon, target)
    assert target.read_bytes() == first


def test_icon_encoding_failure_is_nonfatal_and_writes_nothing(tmp_path, caplog):
    icon = SimpleNamespace(pixmap=lambda *_args: SimpleNamespace(save=lambda *_args: False))
    target = tmp_path / "application.ico"
    assert not identity.save_notification_icon(icon, target)
    assert not target.exists()
    assert "继续启动" in caplog.text


def test_notification_registration_only_writes_own_user_name_and_icon(monkeypatch, tmp_path):
    writes, created = [], []
    registry = SimpleNamespace(HKEY_CURRENT_USER="HKCU", KEY_SET_VALUE=2, REG_EXPAND_SZ=2)
    registry.CreateKeyEx = lambda *args: created.append(args) or nullcontext("app-key")
    registry.SetValueEx = lambda *args: writes.append(args)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(identity.sys, "platform", "win32")
    icon = tmp_path / "application.ico"
    icon.write_bytes(b"icon")
    assert identity.register_notification_assets(icon)
    assert created == [("HKCU", rf"Software\Classes\AppUserModelId\{identity.APP_USER_MODEL_ID}", 0, 2)]
    assert writes == [("app-key", "DisplayName", 0, 2, identity.APPLICATION_NAME),
                      ("app-key", "IconUri", 0, 2, str(icon.resolve()))]


def test_registry_policy_denial_is_nonfatal(monkeypatch, tmp_path, caplog):
    def denied(*_args):
        raise PermissionError("registry policy")
    registry = SimpleNamespace(HKEY_CURRENT_USER="HKCU", KEY_SET_VALUE=2, REG_EXPAND_SZ=2, CreateKeyEx=denied)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(identity.sys, "platform", "win32")
    icon = tmp_path / "application.ico"
    icon.write_bytes(b"icon")
    assert not identity.register_notification_assets(icon)
    assert "继续启动" in caplog.text


@pytest.mark.parametrize("persistent_registration", [False, True])
def test_app_identity_precedes_qapplication_and_tray_creation(monkeypatch, tmp_path, persistent_registration):
    from app import main
    events = []
    class QtApplication:
        @staticmethod
        def instance():
            return None
        def __init__(self, _argv):
            events.append("QApplication")
        def setApplicationName(self, value):
            self.name = value
        def setApplicationDisplayName(self, value):
            self.display_name = value
        def setOrganizationName(self, value):
            self.organization = value
        def setWindowIcon(self, value):
            self.icon = value
        def windowIcon(self):
            return self.icon
        def setQuitOnLastWindowClosed(self, value):
            pass
    class Window:
        @staticmethod
        def _app_icon():
            return "artwork"
        def __init__(self, paths):
            events.append("window-and-tray")
    class Lock:
        def __init__(self, path):
            pass
        def tryLock(self, timeout):
            return True
    paths = SimpleNamespace(project_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(main, "set_process_app_id", lambda: events.append("native-id"))
    monkeypatch.setattr(main, "QApplication", QtApplication)
    monkeypatch.setattr(main, "QLockFile", Lock)
    monkeypatch.setattr(main, "MainWindow", Window)
    monkeypatch.setattr(main.AppPaths, "default", classmethod(lambda cls: paths))
    monkeypatch.setattr(main, "TranslationApplication", lambda paths: SimpleNamespace(initialize=lambda: events.append("storage")))
    monkeypatch.setattr(main, "MainController", lambda *_args: "controller")
    monkeypatch.setattr(main, "configure_notification_assets", lambda *_args: events.append("notification-assets"))
    application, window, controller = main.create_application(register_shell_identity=persistent_registration)
    assert events[:2] == ["native-id", "QApplication"]
    assert events[-1] == "window-and-tray"
    assert ("notification-assets" in events) is persistent_registration
    if persistent_registration:
        assert events.index("notification-assets") < events.index("window-and-tray")
    assert application.name == application.display_name == identity.APPLICATION_NAME
    assert application.organization == identity.ORGANIZATION_NAME
    assert application.icon == "artwork"


def test_normal_entry_point_enables_persistent_shell_identity(monkeypatch):
    from app import main
    options = []
    def stop(**kwargs):
        options.append(kwargs)
        raise RuntimeError("test startup ended")
    monkeypatch.setattr(main, "create_application", stop)
    monkeypatch.setattr(main.QMessageBox, "information", lambda *_args: None)
    assert main.main() == 1
    assert options == [{"register_shell_identity": True}]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell API")
def test_actual_windows_process_id_survives_qapplication_initialization():
    code = '''
import ctypes,json
from app.windows_identity import set_process_app_id,APP_USER_MODEL_ID
assert set_process_app_id()
from PySide6.QtWidgets import QApplication
app=QApplication([])
shell=ctypes.WinDLL('shell32',use_last_error=True)
getter=shell.GetCurrentProcessExplicitAppUserModelID
getter.argtypes=[ctypes.POINTER(ctypes.c_wchar_p)]
getter.restype=ctypes.c_long
value=ctypes.c_wchar_p()
assert getter(ctypes.byref(value))==0
actual=value.value
ole=ctypes.WinDLL('ole32')
ole.CoTaskMemFree.argtypes=[ctypes.c_void_p]
ole.CoTaskMemFree.restype=None
ole.CoTaskMemFree(ctypes.cast(value,ctypes.c_void_p))
print(json.dumps({'native_app_id':actual,'expected':APP_USER_MODEL_ID}))
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    payload = json.loads(result.stdout)
    assert payload["native_app_id"] == identity.APP_USER_MODEL_ID


def _window(qtbot, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda *_args: None)
    window = MainWindow(AppPaths.default())
    qtbot.addWidget(window)
    return window


def test_tray_has_app_tooltip_owned_menu_and_matching_icon(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    assert window.tray_icon.toolTip() == identity.APPLICATION_NAME
    assert window.tray_icon.contextMenu().parent() is window
    assert window.tray_icon.icon().pixmap(32, 32).toImage() == window.windowIcon().pixmap(32, 32).toImage()
    window._allow_quit = True


def test_clicking_tray_notification_restores_maximized_window(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.showMaximized()
    window.close()
    assert not window.isVisible()
    window.tray_icon.messageClicked.emit()
    assert window.isVisible() and window.isMaximized()
    window.tray_icon.hide()
    window._allow_quit = True


def test_close_hint_uses_app_name_and_custom_icon_and_only_shows_once(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    messages = []
    monkeypatch.setattr(window.tray_icon, "showMessage", lambda *args: messages.append(args))
    window.show()
    window.close()
    window.show_from_tray()
    window.close()
    assert len(messages) == 1
    assert messages[0][0] == identity.APPLICATION_NAME
    assert isinstance(messages[0][2], QIcon) and not messages[0][2].isNull()
    window.tray_icon.hide()
    window._allow_quit = True
