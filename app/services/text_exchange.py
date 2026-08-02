from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


TranslationUpdates = dict[tuple[str, str], str]


class TextExchangeService:
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
                updates[(row[0], row[1])] = row[3]
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
        pages = [self._load_metadata(path) for path in metadata_paths]
        payload = {"schema_version": 2, "pages": pages}
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_json(self, input_path: Path) -> dict[str, Any]:
        document = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if document.get("schema_version") != 2 or not isinstance(document.get("pages"), list):
            raise ValueError("翻译JSON版本或结构不受支持")
        return document

    @staticmethod
    def _load_metadata(path: Path) -> dict[str, Any]:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("regions"), list):
            raise ValueError(f"OCR元数据结构无效：{path}")
        return document
