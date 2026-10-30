"""Start the desktop app independently, including environments with a broken pythonw shim."""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def is_windowed_executable(path: Path) -> bool:
    """Check the Windows PE subsystem rather than trusting the executable name."""
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return False
            stream.seek(0x3C)
            offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                return False
            stream.seek(offset + 24 + 68)
            return struct.unpack("<H", stream.read(2))[0] == 2
    except (OSError, struct.error):
        return False


def gui_interpreter(project_root: Path, *, base_prefix: Path | None = None) -> Path:
    scripts = project_root / ".venv" / "Scripts"
    if not (scripts / "python.exe").is_file():
        raise RuntimeError("Python environment is missing. Run scripts/setup.ps1 first.")
    candidate = scripts / "pythonw.exe"
    if is_windowed_executable(candidate):
        return candidate

    # Older uv environments can install identical console shims for python/pythonw.
    # Use CPython's own venv GUI redirector; keep the installed environment intact.
    source = (base_prefix or Path(sys.base_prefix)) / "Lib" / "venv" / "scripts" / "nt" / "pythonw.exe"
    if not is_windowed_executable(source):
        raise RuntimeError("A windowless Python launcher is unavailable. Recreate .venv with scripts/setup.ps1.")
    target = scripts / "manga-translator-gui.exe"
    if target.is_file() and target.read_bytes() == source.read_bytes():
        return target
    with tempfile.NamedTemporaryFile(dir=scripts, suffix=".exe", prefix=".gui-", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copyfile(source, temporary_path)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


def launch(project_root: Path, *, entry_point: Path | None = None) -> subprocess.Popen:
    if os.name != "nt":
        raise RuntimeError("The desktop launcher requires Windows.")
    interpreter = gui_interpreter(project_root)
    script = entry_point or project_root / "run.py"
    if not script.is_file():
        raise RuntimeError(f"Application entry point is missing: {script.name}")
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "startup.log").open("ab") as log:
        return subprocess.Popen(
            [str(interpreter), str(script)], cwd=project_root,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the GUI interpreter without opening the app")
    args = parser.parse_args()
    try:
        if args.check:
            print(f"GUI launcher check passed: {gui_interpreter(PROJECT_ROOT)}")
        else:
            launch(PROJECT_ROOT)
        return 0
    except (OSError, RuntimeError) as error:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "startup.log").open("a", encoding="utf-8") as log:
            log.write(f"Launcher error: {error}\n")
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
