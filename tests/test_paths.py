from pathlib import Path

from app.paths import AppPaths
import app.paths as paths_module


def test_default_paths_follow_checkout_location(tmp_path, monkeypatch) -> None:
    checkout = tmp_path / "漫画翻译器 checkout"
    monkeypatch.setattr(paths_module, "__file__", str(checkout / "app" / "paths.py"))
    paths = AppPaths.default()

    assert paths.project_root == checkout
    assert paths.output_root == checkout / "outputs"
    assert paths.temp_root == checkout / ".cache" / "tmp"
    assert paths.database_path == checkout / "data" / "app.db"


def test_task_directory_rejects_unsafe_task_id() -> None:
    paths = AppPaths.default()

    try:
        paths.task_dir("../outside")
    except ValueError as error:
        assert "任务编号" in str(error)
    else:
        raise AssertionError("unsafe task id should be rejected")

