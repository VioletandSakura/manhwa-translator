"""Isolated release acceptance: real OCR, local text edit, redraw and exports.

First run --prepare-models in the NEW environment while online. Then run without
that flag: outgoing socket connections are rejected throughout acceptance. No
system credential access or translation provider is used in either mode.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import sys
import time
import zipfile


class EmptyCredentials:
    def get_password(self, *_):
        return None

    def set_password(self, *_):
        raise AssertionError('Release smoke must not save credentials')

    def delete_password(self, *_):
        raise AssertionError('Release smoke must not delete credentials')


@contextmanager
def no_network():
    """Fail even accidental first-use model downloads/API calls during smoke."""
    def denied(*_, **__):
        raise AssertionError('Offline release smoke attempted a network connection')
    connect, connect_ex, create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create


def model_manifest() -> dict:
    import rapidocr
    directory = Path(rapidocr.__file__).parent / 'models'
    return {path.name: {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(directory.glob('*.onnx'))}


async def acceptance(output: Path) -> dict:
    import cv2
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from app.domain.enums import PageStatus, TaskStatus
    from app.paths import AppPaths
    from app.security.credentials import CredentialStore
    from app.services.application import TranslationApplication
    from app.services.text_exchange import TextExchangeService
    from app.tasks.pipeline import PipelineOptions
    from app.ui.main_window import MainWindow

    paths = AppPaths(project_root=output / 'runtime', output_root=output / 'outputs', temp_root=output / 'temp')
    app = TranslationApplication(paths, credentials=CredentialStore(EmptyCredentials()))
    app.initialize()
    try:
        source = output / 'generated-hello.png'
        pixels = np.full((240, 900, 3), 255, dtype=np.uint8)
        cv2.putText(pixels, 'HELLO MY FRIEND', (25, 145), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (0, 0, 0), 4)
        success, encoded = cv2.imencode('.png', pixels)
        assert success
        source.write_bytes(encoded.tobytes())
        started = time.perf_counter()
        outcome = await app.process_page(source, paths.output_root / 'hello', PipelineOptions(translate=False))
        ocr_seconds = time.perf_counter() - started
        texts = [region.source_text for region in outcome.result.regions]
        assert any('HELLO' in text.upper() for text in texts), texts
        artifacts = outcome.result.artifacts
        assert app.database.get_task(outcome.task_id).status is TaskStatus.COMPLETED
        assert app.database.get_page(outcome.page_id).status is PageStatus.COMPLETED
        assert np.array_equal(cv2.imdecode(np.frombuffer(artifacts.translated.read_bytes(), np.uint8), cv2.IMREAD_COLOR), pixels)
        exchange = TextExchangeService()
        exchange.export_txt([artifacts.metadata], output / 'ocr.txt')
        exchange.export_json([artifacts.metadata], output / 'ocr.json')
        document = json.loads(artifacts.metadata.read_text(encoding='utf-8'))
        changes = {(artifacts.metadata.stem, region['id']): 'GOOD MORNING' for region in document['regions']}
        assert exchange.apply_translations(artifacts.metadata, changes) == len(document['regions'])
        rendered = await app.rerender_metadata(artifacts.metadata, render_options=PipelineOptions(translate=False))
        assert rendered.is_file() and rendered.read_bytes() != artifacts.inpainted.read_bytes()
        archive_path = app.export_task_zip(outcome.task_id, output / 'translated.zip')
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None and any(name.endswith('.png') for name in archive.namelist())
        qt_app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.resize(1366, 768)
        window.show()
        qt_app.processEvents()
        assert window.tabs.count() == 11
        assert window.grab().save(str(output / 'window.png'))
        window.close()
        qt_app.processEvents()
        return {'passed': True, 'real_ocr_texts': texts, 'ocr_seconds': ocr_seconds,
                'manually_supplied_test_translation': 'GOOD MORNING', 'pages': 1,
                'network_blocked': True, 'translation_api_calls': 0, 'system_credentials_used': False,
                'checks': ['real OCR', 'SQLite completed checkpoint', 'OCR-only preserves original pixels',
                           'TXT/JSON export', 'manual text edit', 'OpenCV erase + Pillow redraw',
                           'translated ZIP CRC', '11-page Qt window screenshot']}
    finally:
        app.database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prepare-models', action='store_true', help='Allow online RapidOCR model bootstrap only; not offline acceptance')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Use a new/empty output directory to preserve earlier evidence')
    output.mkdir(parents=True, exist_ok=True)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['HF_HOME'] = str(output / 'unused-hf-cache')
    os.environ['HF_HUB_OFFLINE'] = '1'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    packages = {name: importlib.metadata.version(name) for name in (
        'manga-translator-personal', 'numpy', 'opencv-python', 'Pillow', 'PySide6-Essentials', 'onnxruntime', 'rapidocr')}
    report = {'python': sys.version, 'executable': sys.executable, 'base_prefix': sys.base_prefix,
              'venv_prefix': sys.prefix, 'platform': platform.platform(), 'machine': platform.machine(),
              'packages': packages, 'models_before': model_manifest()}
    if args.prepare_models:
        from rapidocr import RapidOCR
        engine = RapidOCR()
        del engine
        report.update({'mode': 'online-model-bootstrap', 'models_after': model_manifest(), 'paid_api_calls': 0})
    else:
        # Windows creates an internal loopback socket pair when its event loop
        # starts. Initialize that before forbidding actual acceptance traffic.
        with asyncio.Runner() as runner:
            runner.get_loop()
            with no_network():
                report.update(runner.run(acceptance(output)))
        report.update({'mode': 'offline-release-acceptance', 'models_after': model_manifest()})
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
