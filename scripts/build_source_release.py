"""Build a deterministic, allowlisted source ZIP without local runtime data.

Run with Python 3.12; no third-party dependencies, Git, or installed app needed.
Files are selected by path AND extension; links are never followed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
import zipfile


TOP_LEVEL = {
    'README.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'NOTICE', 'NOTICE.md',
    'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'CODE_OF_CONDUCT.md', 'pyproject.toml', '.gitignore', 'run.py',
    'requirements.txt', 'requirements-ocr.txt', 'requirements-mangaocr.txt',
    'constraints-windows-py312.txt', '启动个人漫画翻译器.bat', '启动个人漫画翻译器.vbs',
}
DIRECTORIES = {'app', 'scripts', 'tests', 'docs', '.github', 'models', 'fonts'}
EXCLUDED_PARTS = {'__pycache__', '.pytest_cache', '.cache', '.git', '.venv',
                  '.artifacts', 'node_modules'}
SOURCE_MANIFEST = 'SOURCE_MANIFEST.json'


def allowed_file(relative: Path) -> bool:
    parts = relative.parts
    if any(part in EXCLUDED_PARTS or part.startswith('.venv-') for part in parts):
        return False
    if len(parts) == 1:
        return parts[0] in TOP_LEVEL
    suffix = relative.suffix.lower()
    if parts[0] == 'models':
        return parts == ('models', 'manifest.json')
    if parts[0] == 'fonts':
        return parts == ('fonts', '.gitkeep')
    if parts[0] == 'app':
        return suffix == '.py' or (parts[:3] == ('app', 'ui', 'assets') and suffix == '.svg')
    if parts[0] == 'scripts':
        return suffix in {'.py', '.ps1'}
    if parts[0] == 'tests':
        return suffix == '.py' or (len(parts) > 2 and parts[1] == 'fixtures' and suffix in {'.json', '.txt', '.csv', '.png', '.svg'})
    if parts[0] == 'docs':
        if len(parts) > 2 and parts[1] == 'samples':
            return relative.as_posix() in {'docs/samples/horizontal.png', 'docs/samples/vertical.png',
                                          'docs/samples/textured.png', 'docs/samples/expected.json'}
        return suffix in {'.md', '.txt'} or (len(parts) > 2 and parts[1] == 'images' and suffix in {'.png', '.jpg', '.svg', '.webp'})
    if parts[0] == '.github':
        return suffix in {'.yml', '.yaml', '.md'}
    return False


def is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def collect_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        dirs[:] = sorted(name for name in dirs
                         if name not in EXCLUDED_PARTS and not name.startswith('.venv-')
                         and (relative_dir.parts or name in DIRECTORIES)
                         and not is_link(current / name))
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root)
            if allowed_file(relative):
                if is_link(path):
                    raise ValueError(f'Refusing linked release file: {relative.as_posix()}')
                files[relative.as_posix()] = path.read_bytes()
    required = {'pyproject.toml', 'README.md', 'run.py', 'app/__init__.py',
                'scripts/setup.ps1', 'scripts/smoke_release.py', 'constraints-windows-py312.txt',
                'LICENSE', 'THIRD_PARTY_NOTICES.md'}
    missing = required - files.keys()
    if missing:
        raise ValueError(f'Incomplete source tree; missing: {", ".join(sorted(missing))}')
    return dict(sorted(files.items()))


def release_version(version: str) -> str:
    return re.sub(r'^(\d+\.\d+\.\d+)a(\d+)$', r'\1-alpha.\2', version)


def build_release(root: Path, output: Path, *, version: str | None = None) -> Path:
    root = root.resolve()
    files = collect_files(root)
    metadata = tomllib.loads(files['pyproject.toml'].decode('utf-8'))['project']
    version = version or release_version(metadata['version'])
    if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+-]*', version):
        raise ValueError('Version may contain only ASCII letters, digits, dot, +, and -')
    folder = f'manga-translator-personal-{version}'
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'{folder}-source.zip'
    manifest = {'schema_version': 1, 'project_version': metadata['version'],
                'release_version': version, 'files': {
                    name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                    for name, data in files.items()}}
    files[SOURCE_MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    temporary = target.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                entry = zipfile.ZipInfo(f'{folder}/{name}', date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(f'{checksum}  {target.name}\n', encoding='ascii')
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--version')
    args = parser.parse_args()
    target = build_release(args.root, args.output_dir or args.root / '.artifacts/release', version=args.version)
    print(target)
    print(target.with_suffix('.zip.sha256').read_text(encoding='ascii').strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
