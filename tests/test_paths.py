from pathlib import Path

from app.paths import AppPaths


def test_default_paths_stay_on_d_drive() -> None:
    paths = AppPaths.default()

    assert paths.project_root == Path(r"D:\Chatgpt\01_Projects\manga-translator-personal")
    assert paths.output_root == Path(r"D:\Chatgpt\04_Documents\manga-translator-results")
    assert paths.temp_root == Path(r"D:\Chatgpt\99_Temp\manga-translator-personal")
    assert all(path.drive.upper() == "D:" for path in paths.runtime_directories())


def test_task_directory_rejects_unsafe_task_id() -> None:
    paths = AppPaths.default()

    try:
        paths.task_dir("../outside")
    except ValueError as error:
        assert "任务编号" in str(error)
    else:
        raise AssertionError("unsafe task id should be rejected")

