from __future__ import annotations

import json
from pathlib import Path

from app.services.text_exchange import TextExchangeService


def _metadata(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_path": "001.png",
                "regions": [
                    {"id": "r1", "source_text": "勇者", "translated_text": "Hero"},
                    {"id": "r2", "source_text": "行こう", "translated_text": "Let's go"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_txt_export_can_be_edited_and_imported_back_into_metadata(tmp_path: Path) -> None:
    metadata = tmp_path / "001.json"
    exchange = tmp_path / "chapter.txt"
    _metadata(metadata)
    service = TextExchangeService()

    service.export_txt([metadata], exchange)
    content = exchange.read_text(encoding="utf-8").replace("Hero", "勇者").replace("Let's go", "出发吧")
    exchange.write_text(content, encoding="utf-8")
    updates = service.import_txt(exchange)
    service.apply_translations(metadata, updates)

    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert [region["translated_text"] for region in saved["regions"]] == ["勇者", "出发吧"]


def test_json_export_combines_pages_and_import_validates_schema(tmp_path: Path) -> None:
    metadata = tmp_path / "001.json"
    output = tmp_path / "chapter.json"
    _metadata(metadata)
    service = TextExchangeService()

    service.export_json([metadata], output)
    document = service.import_json(output)

    assert document["schema_version"] == 2
    assert document["pages"][0]["source_path"] == "001.png"
