from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TranslationUpdates = dict[tuple[str, str], str]


@dataclass(frozen=True, slots=True)
class TranslationImportResult:
    changed_regions: int
    changed_paths: list[Path]
    matched_regions: int


class TextExchangeService:
    """TXT/JSON 人工翻译交换与本地重绘入口。"""

    def export_txt(self, metadata_paths: list[Path], output_path: Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, dialect="excel-tab")
            writer.writerow(["页面", "文本框ID", "原文", "译文"])
            for metadata_path in metadata_paths:
                document = self._load_metadata(metadata_path)
                for region in document.get("regions", []):
                    writer.writerow(
                        [
                            Path(metadata_path).stem,
                            region.get("id", ""),
                            region.get("source_text", ""),
                            region.get("translated_text", ""),
                        ]
                    )

    def import_txt(self, input_path: Path) -> TranslationUpdates:
        updates: TranslationUpdates = {}
        with Path(input_path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, dialect="excel-tab")
            header = next(reader, None)
            if header != ["页面", "文本框ID", "原文", "译文"]:
                raise ValueError("TXT表头不正确，请使用软件导出的文件")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 4:
                    raise ValueError(f"TXT第{row_number}行列数不正确")
                key = (row[0], row[1])
                if not all(key) or key in updates:
                    raise ValueError(f"TXT第{row_number}行页面/文本框ID为空或重复")
                updates[key] = row[3]
        return updates

    def apply_translations(self, metadata_path: Path, updates: TranslationUpdates) -> int:
        metadata_path = Path(metadata_path)
        document = self._load_metadata(metadata_path)
        changed = 0
        page_key = metadata_path.stem
        for region in document.get("regions", []):
            key = (page_key, str(region.get("id", "")))
            if key in updates:
                region["translated_text"] = updates[key]
                changed += 1
        metadata_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return changed

    def export_json(self, metadata_paths: list[Path], output_path: Path) -> None:
        pages = [dict(self._load_metadata(path), page_id=Path(path).stem) for path in metadata_paths]
        payload = {"schema_version": 2, "pages": pages}
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_json(self, input_path: Path) -> dict[str, Any]:
        document = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict) or document.get("schema_version") != 2 or not isinstance(document.get("pages"), list):
            raise ValueError("翻译JSON版本或结构不受支持")
        for page in document["pages"]:
            if not isinstance(page, dict) or not isinstance(page.get("source_path"), str) or not isinstance(page.get("regions"), list):
                raise ValueError("翻译JSON页面缺少原图标识或文本框数组")
            seen = set()
            for region in page["regions"]:
                if (not isinstance(region, dict) or not isinstance(region.get("id"), str)
                        or not region["id"] or region["id"] in seen
                        or not isinstance(region.get("source_text"), str)
                        or not isinstance(region.get("translated_text"), str)):
                    raise ValueError("翻译JSON文本框ID重复、为空，或原文/译文不是文本")
                seen.add(region["id"])
        return document

    def import_into_metadata(self, metadata_paths: list[Path], input_path: Path) -> int:
        """兼容旧调用：返回匹配到的文本框数量。"""
        return self.import_with_result(metadata_paths, input_path).matched_regions

    def import_with_result(self, metadata_paths: list[Path], input_path: Path) -> TranslationImportResult:
        """按页/文本框标识预检全部内容后，只更新译文，保留几何与检查状态。"""
        current = {Path(path): self._load_metadata(path) for path in metadata_paths}
        changed = 0
        matched = 0
        changed_paths: set[Path] = set()
        input_path = Path(input_path)

        def update(path: Path, region: dict, translated: str) -> None:
            nonlocal matched, changed
            matched += 1
            if region.get("translated_text", "") != translated:
                region["translated_text"] = translated
                changed += 1
                changed_paths.add(path)
        if input_path.suffix.lower() == ".txt":
            updates = self.import_txt(input_path)
            known = {(path.stem, region["id"]) for path, page in current.items() for region in page["regions"]}
            if set(updates) - known:
                raise ValueError("TXT包含不属于当前任务的页面或文本框ID")
            for path, page in current.items():
                for region in page["regions"]:
                    if (path.stem, region["id"]) in updates:
                        update(path, region, updates[(path.stem, region["id"])])
        else:
            incoming = self.import_json(input_path)["pages"]
            if len(incoming) != len(current):
                raise ValueError("导入JSON的页面数量与当前任务不一致")
            by_id = {path.stem: path for path in current}
            by_source = {self._source_key(page["source_path"]): path for path, page in current.items()}
            if len(by_id) != len(current) or len(by_source) != len(current):
                raise ValueError("当前任务页面标识重复，无法安全匹配")
            seen = set()
            for page in incoming:
                source_key = self._source_key(page["source_path"])
                path = by_id.get(page["page_id"]) if isinstance(page.get("page_id"), str) else by_source.get(source_key)
                if path is None or path in seen or self._source_key(current[path]["source_path"]) != source_key:
                    raise ValueError("导入JSON的页面标识重复或不属于当前任务")
                seen.add(path)
                existing = {region["id"]: region for region in current[path]["regions"]}
                if len(existing) != len(current[path]["regions"]) or set(existing) != {region["id"] for region in page["regions"]}:
                    raise ValueError(f"{path.stem} 的文本框ID与当前结果不一致")
                for region in page["regions"]:
                    original = existing[region["id"]]
                    if region["source_text"] != original.get("source_text", ""):
                        raise ValueError(f"{path.stem} 的原文发生变化，请重新导出后修改译文")
                    update(path, original, region["translated_text"])
        ordered_paths = [path for path in current if path in changed_paths]
        self._commit_documents({path: current[path] for path in ordered_paths})
        return TranslationImportResult(changed, ordered_paths, matched)

    @staticmethod
    def _source_key(value: str) -> str:
        return value.replace("\\", "/").casefold()

    @staticmethod
    def _write_synced(path: Path, content: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def _restore_transaction(cls, journal: Path, names: list[str]) -> None:
        for name in names:
            if Path(name).name != name or not name.endswith(".json"):
                raise ValueError("译文导入恢复记录无效")
            staged = journal / "restore.tmp"
            cls._write_synced(staged, (journal / "before" / name).read_bytes())
            os.replace(staged, journal.parent / name)

    @classmethod
    def recover_pending(cls, directory: Path) -> int:
        recovered = 0
        for journal in directory.glob(".text-import-*"):
            manifest = journal / "manifest.json"
            if not manifest.is_file():
                # A process died while preparing backups, before replacing files.
                cls._cleanup_journal(journal)
                continue
            if not (journal / "committed").exists():
                cls._restore_transaction(journal, json.loads(manifest.read_text(encoding="utf-8")))
                recovered += 1
            cls._cleanup_journal(journal)
        return recovered

    @staticmethod
    def _cleanup_journal(journal: Path) -> None:
        # Publish cleanup with a rename. A crash during recursive removal must
        # never turn a committed import into an incomplete rollback journal.
        cleanup = journal.with_name(journal.name.replace(".text-import-", ".text-cleanup-", 1))
        os.replace(journal, cleanup)
        shutil.rmtree(cleanup)

    @classmethod
    def _commit_documents(cls, documents: dict[Path, dict]) -> None:
        if not documents:
            return
        parents = {path.parent.resolve() for path in documents}
        if len(parents) != 1:
            raise ValueError("人工译文导入必须属于同一任务的metadata目录")
        directory = parents.pop()
        journal = Path(tempfile.mkdtemp(prefix=".text-import-", dir=directory))
        names = [path.name for path in documents]
        (journal / "before").mkdir()
        (journal / "after").mkdir()
        prepared = False
        try:
            for path, document in documents.items():
                cls._write_synced(journal / "before" / path.name, path.read_bytes())
                cls._write_synced(journal / "after" / path.name, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
            # Atomic publication ensures recovery never reads a partial manifest.
            cls._write_synced(journal / "manifest.tmp", json.dumps(names).encode("utf-8"))
            os.replace(journal / "manifest.tmp", journal / "manifest.json")
            prepared = True
            for path in documents:
                os.replace(journal / "after" / path.name, path)
            cls._write_synced(journal / "committed.tmp", b"ok")
            os.replace(journal / "committed.tmp", journal / "committed")
        except BaseException:
            if prepared:
                cls._restore_transaction(journal, names)
            raise
        finally:
            # If restoration itself fails, preserve the journal for next startup.
            if not prepared or (journal / "committed").exists():
                cls._cleanup_journal(journal)


    @staticmethod
    def _load_metadata(path: Path) -> dict[str, Any]:
        TextExchangeService.recover_pending(Path(path).parent)
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("regions"), list):
            raise ValueError(f"OCR元数据结构无效：{path}")
        return document
