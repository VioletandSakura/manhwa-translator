from pathlib import Path

import pytest

from app.services.output_paths import batch_requires_name, resolve_batch_output


def test_resolve_batch_output_uses_user_named_child_directory(tmp_path: Path) -> None:
    root = tmp_path / "results"

    assert resolve_batch_output(root, "第一卷", require_name=True) == root / "第一卷"


def test_single_image_can_keep_legacy_output_root(tmp_path: Path) -> None:
    assert resolve_batch_output(tmp_path, "", require_name=False) == tmp_path


@pytest.mark.parametrize(
    "name",
    ["", "  ", ".", "..", "../escape", r"..\escape", "a/b", r"a\b", "bad:name", "bad*name"],
)
def test_required_batch_name_rejects_blank_or_unsafe_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        resolve_batch_output(tmp_path, name, require_name=True)


@pytest.mark.parametrize("name", ["CON", "con.txt", "PRN", "AUX", "NUL", "COM1", "LPT9", "tail. "])
def test_batch_name_rejects_windows_reserved_or_ambiguous_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        resolve_batch_output(tmp_path, name, require_name=True)


def test_batch_name_rejects_existing_non_empty_directory(tmp_path: Path) -> None:
    existing = tmp_path / "第一卷"
    existing.mkdir()
    (existing / "old.png").write_bytes(b"old")

    with pytest.raises(ValueError, match="已经存在"):
        resolve_batch_output(tmp_path, "第一卷", require_name=True)


def test_batch_requires_name_for_multi_image_folder_zip_and_epub(tmp_path: Path) -> None:
    image = tmp_path / "1.png"
    second = tmp_path / "2.png"
    folder = tmp_path / "chapter"
    folder.mkdir()

    assert not batch_requires_name([image])
    assert batch_requires_name([image, second])
    assert batch_requires_name([folder])
    assert batch_requires_name([tmp_path / "chapter.zip"])
    assert batch_requires_name([tmp_path / "chapter.epub"])
