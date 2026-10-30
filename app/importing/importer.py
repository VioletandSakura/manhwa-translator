from __future__ import annotations

import re
import shutil
import tempfile
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_NUMBER_PART = re.compile(r"(\d+)")


class UnsafeArchiveError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportedPage:
    index: int
    source_path: Path
    original_name: str
    chapter: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportedBatch:
    pages: list[ImportedPage]
    workspace: Path | None


class InputImporter:
    """导入图片、文件夹、ZIP/CBZ 与 EPUB，并防御路径穿越和压缩炸弹。"""

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_entries: int = 10_000,
        max_uncompressed_bytes: int = 4 * 1024**3,
        max_compression_ratio: int = 250,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio

    def import_sources(self, sources: list[Path]) -> ImportedBatch:
        """按自然顺序导入全部输入，返回页面批次与临时工作区。"""
        if not sources:
            raise ValueError("请选择至少一个输入文件或文件夹")
        imported: list[tuple[Path, str, dict[str, object]]] = []
        workspace: Path | None = None
        for raw_source in sources:
            source = Path(raw_source)
            if source.is_dir():
                images = sorted(self._folder_images(source), key=lambda item: _natural_key(str(item.relative_to(source))))
                imported.extend((image, source.name, {"input_type": "folder"}) for image in images)
                continue
            if not source.is_file():
                raise FileNotFoundError(f"输入不存在：{source}")
            suffix = source.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                imported.append((source, source.parent.name, {"input_type": "image"}))
            elif suffix in {".zip", ".cbz"}:
                workspace = workspace or self._new_workspace()
                imported.extend(self._import_zip(source, workspace))
            elif suffix == ".epub":
                workspace = workspace or self._new_workspace()
                imported.extend(self._import_epub(source, workspace))
            else:
                raise ValueError(f"不支持的输入格式：{source.suffix}")
        if not imported:
            raise ValueError("输入中没有找到支持的漫画图片")
        # All output artifacts are keyed by the source stem, including on
        # Windows where case-only differences also refer to the same filename.
        # Stage only ambiguous pages; leave ordinary inputs at their old paths.
        stem_counts = Counter(path.stem.casefold() for path, _, _ in imported)
        if any(count > 1 for count in stem_counts.values()):
            workspace = workspace or self._new_workspace()
            destination = Path(tempfile.mkdtemp(prefix="pages-", dir=workspace))
            used_stems = set(stem_counts)
            unique = []
            for index, (path, chapter, metadata) in enumerate(imported):
                if stem_counts[path.stem.casefold()] > 1:
                    stem = f"{index + 1:06d}_{path.stem[:64]}"
                    candidate, counter = stem, 0
                    while candidate.casefold() in used_stems:
                        counter += 1
                        candidate = f"{stem}_{counter}"
                    used_stems.add(candidate.casefold())
                    target = destination / f"{candidate}{path.suffix}"
                    shutil.copyfile(path, target)
                    metadata = {**metadata, "original_name": metadata.get("original_name", path.name)}
                    path = target
                unique.append((path, chapter, metadata))
            imported = unique
        pages = [
            ImportedPage(
                index=index,
                source_path=path,
                original_name=path.name if "original_name" not in metadata else str(metadata["original_name"]),
                chapter=chapter,
                metadata={key: value for key, value in metadata.items() if key != "original_name"},
            )
            for index, (path, chapter, metadata) in enumerate(imported)
        ]
        return ImportedBatch(pages=pages, workspace=workspace)

    @staticmethod
    def _folder_images(folder: Path) -> list[Path]:
        return [path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]

    def _new_workspace(self) -> Path:
        workspace = self.workspace_root / uuid.uuid4().hex
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    def _validate_archive(self, archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
        files = [info for info in archive.infolist() if not info.is_dir()]
        if len(files) > self.max_entries:
            raise UnsafeArchiveError("压缩包条目过多，已拒绝解压")
        if sum(info.file_size for info in files) > self.max_uncompressed_bytes:
            raise UnsafeArchiveError("压缩包解压后体积过大，已拒绝解压")
        validated: dict[str, zipfile.ZipInfo] = {}
        for info in files:
            safe_name = _safe_member_name(info.filename)
            if info.file_size / max(info.compress_size, 1) > self.max_compression_ratio:
                raise UnsafeArchiveError("压缩比异常，疑似压缩炸弹，已拒绝解压")
            validated[safe_name] = info
        return validated

    def _import_zip(self, source: Path, workspace: Path) -> list[tuple[Path, str, dict[str, object]]]:
        """解压 ZIP/CBZ 中的图片，保持自然排序。"""
        # Different inputs can have the same archive name. They must not share
        # an extraction directory before the final page-name collision pass.
        destination = Path(tempfile.mkdtemp(prefix=f"{source.stem[:48]}-", dir=workspace))
        with zipfile.ZipFile(source) as archive:
            members = self._validate_archive(archive)
            image_names = sorted(
                (name for name in members if PurePosixPath(name).suffix.lower() in IMAGE_EXTENSIONS),
                key=_natural_key,
            )
            return [
                (
                    self._extract_member(archive, members[name], destination, index),
                    source.stem,
                    {"input_type": "zip", "archive_member": name, "original_name": PurePosixPath(name).name},
                )
                for index, name in enumerate(image_names)
            ]

    def _import_epub(self, source: Path, workspace: Path) -> list[tuple[Path, str, dict[str, object]]]:
        """按 EPUB Spine 顺序导入页面图片。"""
        destination = Path(tempfile.mkdtemp(prefix=f"{source.stem[:48]}-", dir=workspace))
        with zipfile.ZipFile(source) as archive:
            members = self._validate_archive(archive)
            container = self._parse_xml(self._read_member(archive, members, "META-INF/container.xml"), "container.xml")
            rootfile = next((item.attrib.get("full-path") for item in container.iter() if _local_name(item.tag) == "rootfile"), None)
            if not rootfile:
                raise ValueError("EPUB缺少OPF入口")
            opf_name = _safe_member_name(unquote(rootfile))
            opf_dir, manifest, spine_items = self._epub_manifest(archive, members, opf_name)
            ordered: list[tuple[str, str, int]] = []
            seen: set[str] = set()
            for spine_index, item_id in enumerate(spine_items):
                href, media_type = manifest.get(item_id, ("", ""))
                member_name = _resolve_member(opf_dir, href)
                if _is_image(member_name, media_type):
                    self._append_epub_page(ordered, seen, member_name, PurePosixPath(member_name).stem, spine_index)
                    continue
                if member_name not in members or "html" not in media_type:
                    continue
                for image_name in self._html_image_members(archive, members, member_name):
                    self._append_epub_page(ordered, seen, image_name, PurePosixPath(member_name).stem, spine_index)
            for item_id, (href, media_type) in manifest.items():
                member_name = _resolve_member(opf_dir, href)
                if _is_image(member_name, media_type):
                    self._append_epub_page(ordered, seen, member_name, item_id, len(spine_items))
            imported: list[tuple[Path, str, dict[str, object]]] = []
            for index, (member_name, chapter, spine_index) in enumerate(ordered):
                if member_name not in members:
                    raise ValueError(f"EPUB引用的图片不存在：{member_name}")
                path = self._extract_member(archive, members[member_name], destination, index)
                imported.append(
                    (
                        path,
                        chapter,
                        {
                            "input_type": "epub",
                            "archive_member": member_name,
                            "spine_index": spine_index,
                            "original_name": PurePosixPath(member_name).name,
                        },
                    )
                )
            return imported

    @staticmethod
    def _append_epub_page(
        ordered: list[tuple[str, str, int]],
        seen: set[str],
        member_name: str,
        chapter: str,
        spine_index: int,
    ) -> None:
        """按去重规则追加一个 EPUB 页面。"""
        if PurePosixPath(member_name).suffix.lower() not in IMAGE_EXTENSIONS or member_name in seen:
            return
        seen.add(member_name)
        ordered.append((member_name, chapter, spine_index))

    @classmethod
    def _epub_manifest(
        cls,
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        opf_name: str,
    ) -> tuple[PurePosixPath, dict[str, tuple[str, str]], list[str]]:
        """解析 OPF 清单与 Spine 顺序。"""
        opf = cls._parse_xml(cls._read_member(archive, members, opf_name), "OPF")
        opf_dir = PurePosixPath(opf_name).parent
        manifest = {
            item.attrib["id"]: (item.attrib.get("href", ""), item.attrib.get("media-type", ""))
            for item in opf.iter()
            if _local_name(item.tag) == "item" and item.attrib.get("id")
        }
        spine_items = [
            item.attrib.get("idref", "")
            for item in opf.iter()
            if _local_name(item.tag) == "itemref"
        ]
        return opf_dir, manifest, spine_items

    @staticmethod
    def _html_image_members(
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        member_name: str,
    ) -> list[str]:
        """收集 HTML 文档里引用的图片成员名。"""
        document = InputImporter._parse_xml(archive.read(members[member_name]), member_name)
        document_dir = PurePosixPath(member_name).parent
        images: list[str] = []
        for element in document.iter():
            if _local_name(element.tag) not in {"img", "image"}:
                continue
            href_value = (
                element.attrib.get("src")
                or element.attrib.get("href")
                or element.attrib.get("{http://www.w3.org/1999/xlink}href")
            )
            if href_value:
                images.append(_resolve_member(document_dir, href_value))
        return images

    @staticmethod
    def _read_member(archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str) -> bytes:
        safe_name = _safe_member_name(name)
        if safe_name not in members:
            raise ValueError(f"压缩包缺少必要文件：{safe_name}")
        return archive.read(members[safe_name])

    @staticmethod
    def _parse_xml(content: bytes, label: str) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise ValueError(f"无法解析EPUB {label}") from error

    @staticmethod
    def _extract_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path, index: int) -> Path:
        original_name = PurePosixPath(_safe_member_name(info.filename)).name
        target = destination / original_name
        counter = 0
        while target.exists():
            prefix = f"{index:06d}" if counter == 0 else f"{index:06d}_{counter}"
            target = destination / f"{prefix}_{original_name}"
            counter += 1
        with archive.open(info) as source, target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        return target


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _NUMBER_PART.split(value))


def _safe_member_name(value: str) -> str:
    normalized = unquote(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or "\x00" in normalized or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeArchiveError(f"压缩包包含不安全路径：{value}")
    if path.parts and ":" in path.parts[0]:
        raise UnsafeArchiveError(f"压缩包包含不安全路径：{value}")
    return path.as_posix()


def _resolve_member(base: PurePosixPath, href: str) -> str:
    clean_href = unquote(href.split("#", 1)[0].split("?", 1)[0])
    return _safe_member_name((base / clean_href).as_posix())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_image(member_name: str, media_type: str) -> bool:
    return media_type.startswith("image/") or PurePosixPath(member_name).suffix.lower() in IMAGE_EXTENSIONS
