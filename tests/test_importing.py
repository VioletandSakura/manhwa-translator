from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.importing.importer import InputImporter, UnsafeArchiveError


def _write_image(path: Path, content: bytes = b"image") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_importer_naturally_orders_multiple_images_and_folder(tmp_path: Path) -> None:
    chapter = tmp_path / "chapter"
    _write_image(chapter / "10.png")
    _write_image(chapter / "2.jpg")
    _write_image(chapter / "notes.txt")
    extra = tmp_path / "1.webp"
    _write_image(extra)

    result = InputImporter(tmp_path / "imports").import_sources([chapter, extra])

    assert [page.original_name for page in result.pages] == ["2.jpg", "10.png", "1.webp"]
    assert [page.index for page in result.pages] == [0, 1, 2]


def test_zip_import_extracts_images_in_natural_order(tmp_path: Path) -> None:
    archive = tmp_path / "chapter.zip"
    with zipfile.ZipFile(archive, "w") as book:
        book.writestr("pages/10.png", b"ten")
        book.writestr("pages/2.png", b"two")
        book.writestr("readme.txt", b"ignored")

    result = InputImporter(tmp_path / "imports").import_sources([archive])

    assert [page.original_name for page in result.pages] == ["2.png", "10.png"]
    assert [page.source_path.name for page in result.pages] == ["2.png", "10.png"]
    assert [page.source_path.read_bytes() for page in result.pages] == [b"two", b"ten"]


def test_zip_import_rejects_path_traversal_before_extracting(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as book:
        book.writestr("../escape.png", b"bad")

    with pytest.raises(UnsafeArchiveError, match="不安全"):
        InputImporter(tmp_path / "imports").import_sources([archive])

    assert not (tmp_path / "escape.png").exists()


def test_epub_uses_container_opf_and_spine_page_order(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    container = """<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>"""
    opf = """<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <manifest>
        <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
        <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
        <item id="p10" href="images/10.png" media-type="image/png"/>
        <item id="p2" href="images/2.png" media-type="image/png"/>
      </manifest>
      <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
    </package>"""
    chapter1 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="images/2.png"/></body></html>'
    chapter2 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="images/10.png"/></body></html>'
    with zipfile.ZipFile(epub, "w") as book:
        book.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        book.writestr("META-INF/container.xml", container)
        book.writestr("OPS/book.opf", opf)
        book.writestr("OPS/chapter1.xhtml", chapter1)
        book.writestr("OPS/chapter2.xhtml", chapter2)
        book.writestr("OPS/images/2.png", b"two")
        book.writestr("OPS/images/10.png", b"ten")

    result = InputImporter(tmp_path / "imports").import_sources([epub])

    assert [page.original_name for page in result.pages] == ["2.png", "10.png"]
    assert [page.chapter for page in result.pages] == ["chapter1", "chapter2"]
    assert result.pages[0].metadata["spine_index"] == 0
