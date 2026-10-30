from pathlib import Path
import struct

from scripts.launch_gui import gui_interpreter, is_windowed_executable


def _executable(path: Path, subsystem: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 128 + 24 + 68, subsystem)
    path.write_bytes(data)


def test_gui_launcher_checks_subsystem_instead_of_name(tmp_path: Path) -> None:
    misleading_name = tmp_path / "pythonw.exe"
    _executable(misleading_name, 3)
    assert not is_windowed_executable(misleading_name)
    _executable(misleading_name, 2)
    assert is_windowed_executable(misleading_name)
    misleading_name.write_bytes(b"not an executable")
    assert not is_windowed_executable(misleading_name)


def test_gui_launcher_preserves_a_working_pythonw(tmp_path: Path) -> None:
    root = tmp_path / "project with spaces"
    _executable(root / ".venv/Scripts/python.exe", 3)
    existing = root / ".venv/Scripts/pythonw.exe"
    _executable(existing, 2)
    assert gui_interpreter(root, base_prefix=tmp_path / "missing-base") == existing


def test_console_pythonw_uses_cpython_gui_redirector_without_rebuilding_venv(tmp_path: Path) -> None:
    root = tmp_path / "project with spaces"
    base = tmp_path / "base-python"
    _executable(root / ".venv/Scripts/python.exe", 3)
    original = root / ".venv/Scripts/pythonw.exe"
    _executable(original, 3)
    standard_gui = base / "Lib/venv/scripts/nt/pythonw.exe"
    _executable(standard_gui, 2)

    repaired = gui_interpreter(root, base_prefix=base)

    assert repaired != original
    assert is_windowed_executable(repaired)
    assert repaired.read_bytes() == standard_gui.read_bytes()
    assert not is_windowed_executable(original)
    modified = repaired.stat().st_mtime_ns
    assert gui_interpreter(root, base_prefix=base) == repaired
    assert repaired.stat().st_mtime_ns == modified
