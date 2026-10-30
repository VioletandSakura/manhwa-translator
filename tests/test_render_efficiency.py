from __future__ import annotations

import asyncio
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.domain.models import TextRegion
from app.inpainting.opencv_inpainter import OpenCVInpainter
from app.paths import AppPaths
from app.security.credentials import CredentialStore
from app.services.application import TranslationApplication
from app.services.text_exchange import TextExchangeService
from app.tasks.pipeline import PagePipeline, PipelineOptions
from test_application_services import MemoryCredentialBackend


def _application(tmp_path: Path) -> TranslationApplication:
    paths = AppPaths(tmp_path / "runtime", tmp_path / "results", tmp_path / "temp")
    app = TranslationApplication(paths, credentials=CredentialStore(MemoryCredentialBackend()))
    app.initialize()
    return app


def _page(tmp_path: Path):
    output = tmp_path / "output"
    for name in ("metadata", "inpainted", "translated", "original"):
        (output / name).mkdir(parents=True)
    source = np.full((180, 300, 3), 245, dtype=np.uint8)
    cv2.rectangle(source, (25, 35), (125, 75), (0, 0, 0), -1)
    cv2.rectangle(source, (195, 35), (265, 75), (0, 0, 0), -1)
    original = output / "original" / "page.png"
    PagePipeline._write_image(original, source)
    metadata = output / "metadata" / "page.json"
    document = {"source_path": "page.png", "regions": [
        {"id": "dialogue", "source_text": "HELLO FRIEND", "translated_text": "Hello",
         "polygon": [[15, 25], [140, 25], [140, 90], [15, 90]], "needs_review": False,
         "metadata": {"source_polygon": [[15, 25], [140, 25], [140, 90], [15, 90]]}},
        {"id": "effect", "source_text": "BAM", "translated_text": "Effect",
         "polygon": [[185, 25], [275, 25], [275, 90], [185, 90]], "needs_review": True},
    ]}
    _save(metadata, document)
    return metadata, original, source


def _save(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CountingInpainter(OpenCVInpainter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def inpaint_mask(self, image, mask):
        self.calls += 1
        return await super().inpaint_mask(image, mask)


def test_style_text_and_target_geometry_reuse_verified_base_across_restart(tmp_path, monkeypatch):
    app = _application(tmp_path)
    metadata, _original, _source = _page(tmp_path)
    inpainter = CountingInpainter()
    monkeypatch.setattr(app, "_build_inpainter", lambda _engine: inpainter)
    first_result = asyncio.run(app.rerender_metadata(metadata)).read_bytes()
    cleaned = metadata.parent.parent / "inpainted/page_inpainted.png"
    cleaned_before = cleaned.read_bytes()
    cleaned_time = cleaned.stat().st_mtime_ns
    document = _load(metadata)
    dialogue = document["regions"][0]
    dialogue["translated_text"] = "A different translation"
    dialogue["metadata"].update({"font_size": 18, "color": "#d65a38", "alignment": "right"})
    dialogue["polygon"] = [[45, 110], [245, 110], [245, 165], [45, 165]]
    _save(metadata, document)
    # A separate service instance uses only persisted, verified metadata/file.
    reopened = _application(tmp_path)
    monkeypatch.setattr(reopened, "_build_inpainter", lambda _engine: inpainter)
    second_result = asyncio.run(reopened.rerender_metadata(metadata)).read_bytes()
    assert inpainter.calls == 1
    assert cleaned.read_bytes() == cleaned_before
    assert cleaned.stat().st_mtime_ns == cleaned_time
    assert second_result != first_result
    assert _load(metadata)["inpaint_cache"]["version"] == 1
    app.database.close()
    reopened.database.close()


@pytest.mark.parametrize("change", ["source", "source_polygon", "mask", "review", "engine"])
def test_inpainting_cache_invalidates_when_erasure_inputs_change(tmp_path, monkeypatch, change):
    app = _application(tmp_path)
    metadata, original, source = _page(tmp_path)
    inpainter = CountingInpainter()
    monkeypatch.setattr(app, "_build_inpainter", lambda _engine: inpainter)
    asyncio.run(app.rerender_metadata(metadata))
    document = _load(metadata)
    engine = "opencv"
    if change == "source":
        source[130:140, 20:30] = 0
        PagePipeline._write_image(original, source)
    elif change == "source_polygon":
        document["regions"][0]["metadata"]["source_polygon"] = [[15, 25], [150, 25], [150, 95], [15, 95]]
    elif change == "mask":
        mask = np.zeros(source.shape[:2], dtype=np.uint8)
        mask[120:145, 20:45] = 255
        PagePipeline._write_image(metadata.with_name("page_mask.png"), mask)
        document["editor_state"] = {"mask_path": "page_mask.png"}
    elif change == "review":
        document["regions"][0]["metadata"]["review_decision"] = "rejected"
    else:
        engine = "lama_onnx"
    _save(metadata, document)
    asyncio.run(app.rerender_metadata(metadata, inpainting_engine=engine))
    assert inpainter.calls == 2
    rendered = PagePipeline._read_image(metadata.parent.parent / "translated/page_translated.png")
    if change == "review":
        assert np.array_equal(rendered, source)
    # Pending effect pixels remain original even when a base cache is used.
    assert np.array_equal(rendered[25:90, 185:275], source[25:90, 185:275])
    app.database.close()


@pytest.mark.parametrize("damage", ["missing", "changed_pixels", "broken_png", "old_metadata"])
def test_old_or_damaged_inpainted_files_are_never_blindly_reused(tmp_path, monkeypatch, damage):
    app = _application(tmp_path)
    metadata, _original, source = _page(tmp_path)
    inpainter = CountingInpainter()
    monkeypatch.setattr(app, "_build_inpainter", lambda _engine: inpainter)
    asyncio.run(app.rerender_metadata(metadata))
    base = metadata.parent.parent / "inpainted/page_inpainted.png"
    expected = base.read_bytes()
    if damage == "missing":
        base.unlink()
    elif damage == "changed_pixels":
        PagePipeline._write_image(base, np.zeros_like(source))
    elif damage == "broken_png":
        base.write_bytes(b"not an image")
    else:
        document = _load(metadata)
        document.pop("inpaint_cache")
        _save(metadata, document)
    asyncio.run(app.rerender_metadata(metadata))
    assert inpainter.calls == 2
    assert base.read_bytes() == expected
    app.database.close()


def test_lama_model_session_cache_is_single_entry_and_model_changes_invalidate_base(tmp_path, monkeypatch):
    import app.services.application as module
    app = _application(tmp_path)
    metadata, _original, _source = _page(tmp_path)
    model = app.paths.models_dir / "lama.onnx"
    model.write_bytes(b"first model fixture")
    created = []
    class FakeLaMa(CountingInpainter):
        def __init__(self, path):
            super().__init__()
            created.append(self)
    monkeypatch.setattr(module, "LaMaONNXInpainter", FakeLaMa)
    asyncio.run(app.rerender_metadata(metadata, inpainting_engine="lama_onnx"))
    document = _load(metadata)
    document["regions"][0]["metadata"]["color"] = "#ff0000"
    _save(metadata, document)
    asyncio.run(app.rerender_metadata(metadata, inpainting_engine="lama_onnx"))
    assert len(created) == 1 and created[0].calls == 1
    assert app._build_inpainter("lama_onnx") is app._lama_cache[1]
    model.write_bytes(b"replacement model fixture with changed size")
    asyncio.run(app.rerender_metadata(metadata, inpainting_engine="lama_onnx"))
    assert len(created) == 2 and created[1].calls == 1
    assert app._lama_cache[1].primary is created[1]
    app.database.close()


def test_ocr_only_skips_empty_inpainting_and_rendering_and_encodes_png_once(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    image = np.full((100, 180, 3), 255, dtype=np.uint8)
    cv2.putText(image, "Hello", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    PagePipeline._write_image(source, image)
    source_bytes = source.read_bytes()
    source_decoded = PagePipeline._read_image(source)
    class Detector:
        async def detect(self, image):
            return [TextRegion(id="r", polygon=[(0, 0), (180, 0), (180, 100), (0, 100)])]
    class OCR:
        async def recognize(self, image, regions):
            regions[0].source_text = "Hello"
            regions[0].confidence = 0.95
            return regions
    class Unused:
        async def translate(self, *_args):
            pytest.fail("OCR-only must not translate")
        async def inpaint(self, *_args):
            pytest.fail("Empty erasure must not inpaint")
        async def render(self, *_args):
            pytest.fail("Empty rendering must not convert through Pillow")
    encodes = []
    reads = []
    encode = cv2.imencode
    read = Path.read_bytes
    def count_encode(extension, pixels, *args, **kwargs):
        encodes.append(extension)
        return encode(extension, pixels, *args, **kwargs)
    def count_read(path):
        if path == source:
            reads.append(True)
        return read(path)
    monkeypatch.setattr(cv2, "imencode", count_encode)
    monkeypatch.setattr(Path, "read_bytes", count_read)
    pipeline = PagePipeline(detector=Detector(), ocr=OCR(), translator=Unused(), inpainter=Unused(), renderer=Unused())
    result = asyncio.run(pipeline.process(source, tmp_path / "out", PipelineOptions(translate=False)))
    assert encodes == [".png"]
    assert reads == [True]
    assert result.artifacts.original.read_bytes() == source_bytes
    assert np.array_equal(PagePipeline._read_image(result.artifacts.inpainted), source_decoded)
    assert np.array_equal(PagePipeline._read_image(result.artifacts.translated), source_decoded)
    assert result.artifacts.inpainted.read_bytes() == result.artifacts.translated.read_bytes()


def _exchange_pages(tmp_path):
    paths = [tmp_path / "1.json", tmp_path / "2.json"]
    for index, path in enumerate(paths, 1):
        _save(path, {"source_path": f"chapter/{index}.png", "regions": [
            {"id": f"r{index}", "source_text": f"original{index}", "translated_text": f"translation{index}"},
        ]})
    return paths


@pytest.mark.parametrize("extension", ["json", "txt"])
def test_unchanged_translation_import_does_not_write_files_or_transaction(tmp_path, monkeypatch, extension):
    paths = _exchange_pages(tmp_path)
    service = TextExchangeService()
    incoming = tmp_path / f"translations.{extension}"
    getattr(service, f"export_{extension}")(paths, incoming)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
    commits = []
    monkeypatch.setattr(service, "_commit_documents", lambda documents: commits.append(documents) if documents else None)
    result = service.import_with_result(paths, incoming)
    assert result.changed_regions == 0 and result.changed_paths == [] and result.matched_regions == 2
    assert commits == []
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths] == before
    assert service.import_into_metadata(paths, incoming) == 2


@pytest.mark.parametrize("extension", ["json", "txt"])
def test_translation_import_only_commits_actual_changed_page_after_validating_all(tmp_path, monkeypatch, extension):
    paths = _exchange_pages(tmp_path)
    service = TextExchangeService()
    incoming = tmp_path / f"translations.{extension}"
    getattr(service, f"export_{extension}")(paths, incoming)
    incoming.write_text(incoming.read_text(encoding="utf-8-sig").replace("translation2", "new translation"), encoding="utf-8-sig")
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
    result = service.import_with_result(paths, incoming)
    assert result.changed_regions == 1 and result.changed_paths == [paths[1]]
    assert result.matched_regions == 2
    assert (paths[0].read_bytes(), paths[0].stat().st_mtime_ns) == before[0]
    assert _load(paths[1])["regions"][0]["translated_text"] == "new translation"
    assert not list(tmp_path.glob(".text-import-*"))


def test_invalid_unchanged_page_prevents_other_changed_page_being_written(tmp_path):
    paths = _exchange_pages(tmp_path)
    service = TextExchangeService()
    incoming = tmp_path / "translations.json"
    service.export_json(paths, incoming)
    document = service.import_json(incoming)
    document["pages"][0]["regions"][0]["source_text"] = "wrong source"
    document["pages"][1]["regions"][0]["translated_text"] = "new"
    _save(incoming, document)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError, match="原文发生变化"):
        service.import_with_result(paths, incoming)
    assert [path.read_bytes() for path in paths] == before
