import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.build_source_release import build_release, collect_files, release_version


def source_tree(root: Path) -> None:
    for name, content in {
        'pyproject.toml': '[project]\nversion="0.1.0a1"\n',
        'README.md': 'Public instructions', 'run.py': 'pass', 'app/__init__.py': '',
        'app/ui/assets/check.svg': '<svg/>', 'scripts/setup.ps1': 'Write-Host ok',
        'scripts/smoke_release.py': 'pass', 'constraints-windows-py312.txt': 'numpy==2.5.1',
        'LICENSE': 'license', 'THIRD_PARTY_NOTICES.md': 'upstream licenses', 'docs/INSTALL.md': 'installation',
        '.github/ISSUE_TEMPLATE/bug.yml': 'name: Bug', 'tests/test_sample.py': 'pass',
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')


def test_source_release_excludes_private_runtime_and_verifies_every_file(tmp_path):
    root = tmp_path / 'source with spaces'
    source_tree(root)
    for name in ['.env', 'private.key', 'data/app.db', 'logs/startup.log', 'outputs/page.png',
                 '.cache/model.onnx', '.git/config', '.venv/secret.py', '.artifacts/token.txt',
                 'app/__pycache__/private.py', 'app/debug.key', 'docs/token.key',
                 'scripts/local.lock', '.github/api.key', 'models/private.onnx', 'fonts/private.ttf',
                 'docs/samples/private.png', 'docs/samples/private.json']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('PRIVATE-SENTINEL', encoding='utf-8')
    target = build_release(root, tmp_path / 'out')
    assert target.name == 'manga-translator-personal-0.1.0-alpha.1-source.zip'
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        blobs = {name.split('/', 1)[1]: archive.read(name) for name in archive.namelist()}
    assert all(b'PRIVATE-SENTINEL' not in content for content in blobs.values())
    manifest = json.loads(blobs.pop('SOURCE_MANIFEST.json'))
    assert set(manifest['files']) == set(blobs)
    for name, content in blobs.items():
        assert manifest['files'][name] == {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
    assert 'LICENSE' in blobs and 'app/ui/assets/check.svg' in blobs
    assert target.with_suffix('.zip.sha256').read_text().startswith(hashlib.sha256(target.read_bytes()).hexdigest())


def test_source_archive_is_reproducible_and_does_not_include_output_inside_tree(tmp_path):
    source_tree(tmp_path)
    target = build_release(tmp_path, tmp_path / '.artifacts/release')
    original = target.read_bytes()
    assert build_release(tmp_path, tmp_path / '.artifacts/release').read_bytes() == original


def test_source_release_refuses_incomplete_checkout(tmp_path):
    with pytest.raises(ValueError, match='Incomplete source tree'):
        collect_files(tmp_path)


@pytest.mark.parametrize('version', ['../escape', '/tmp/output', 'v1/../../private', 'x\\file'])
def test_source_release_version_cannot_escape_output(tmp_path, version):
    source_tree(tmp_path)
    with pytest.raises(ValueError, match='Version'):
        build_release(tmp_path, tmp_path / 'out', version=version)


def test_version_mapping_preserves_normal_versions():
    assert release_version('0.1.0a1') == '0.1.0-alpha.1'
    assert release_version('1.2.3') == '1.2.3'


def test_release_includes_only_explicit_public_samples_and_model_inventory(tmp_path):
    source_tree(tmp_path)
    for name in ['docs/samples/horizontal.png', 'docs/samples/vertical.png', 'docs/samples/textured.png',
                 'docs/samples/expected.json', 'models/manifest.json', 'fonts/.gitkeep']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'public')
    files = collect_files(tmp_path)
    assert files['docs/samples/horizontal.png'] == b'public'
    assert files['models/manifest.json'] == b'public'
    assert files['fonts/.gitkeep'] == b'public'


def test_release_smoke_rejects_network_and_restores_socket_functions():
    import socket
    from scripts.smoke_release import no_network
    connect = socket.socket.connect
    create = socket.create_connection
    with no_network():
        with pytest.raises(AssertionError, match='network connection'):
            socket.create_connection(('example.invalid', 443))
        with socket.socket() as client:
            with pytest.raises(AssertionError, match='network connection'):
                client.connect(('192.0.2.1', 443))
    assert socket.socket.connect is connect
    assert socket.create_connection is create
