param([switch]$Dev, [string]$Python = '')

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# pip's isolated build environment adds deep paths. Keep TEMP independent of
# the extracted source folder to work on Windows without LongPathsEnabled.
$TempRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('Temp\mts-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$PipCache = Join-Path $ProjectRoot '.cache\pip'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Constraints = Join-Path $ProjectRoot 'constraints-windows-py312.txt'

function Assert-SupportedPython([string]$Executable, [string[]]$PrefixArgs = @()) {
    & $Executable @PrefixArgs -c 'import sys,struct; assert sys.platform == "win32" and sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8, "Install 64-bit Python 3.12 for Windows."'
    if ($LASTEXITCODE -ne 0) { throw 'This release requires 64-bit Python 3.12 for Windows. Use -Python with its python.exe path.' }
}

if (-not (Test-Path -LiteralPath $Constraints)) { throw 'The release constraints file is missing. Extract the complete source archive again.' }

New-Item -ItemType Directory -Force -Path $ProjectRoot, $TempRoot, $PipCache | Out-Null
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = $PipCache
$env:HF_HOME = Join-Path $TempRoot 'huggingface'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonArgs = @()
    if ($Python) {
        $BasePython = $Python
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $BasePython = 'py'
        $PythonArgs = @('-3.12')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BasePython = 'python'
    } else { throw 'Install 64-bit Python 3.12 for Windows first, then rerun setup.' }
    Assert-SupportedPython $BasePython $PythonArgs
    & $BasePython @PythonArgs -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create .venv. Check Python 3.12 and folder write permissions.' }
}
# Qt contains deep QML object paths. Install only that wheel through an
# extended-length prefix; keep Python's normal prefix for other packages whose
# wheels contain relative ../../Scripts paths (not valid after \\?\).
$VenvPrefix = Join-Path $ProjectRoot '.venv'
if ($VenvPrefix.StartsWith('\\')) {
    $ExtendedVenvPrefix = '\\?\UNC\' + $VenvPrefix.Substring(2)
} else {
    $ExtendedVenvPrefix = '\\?\' + $VenvPrefix
}
Assert-SupportedPython $VenvPython
# Some Python environment tools omit pip. Bootstrap the bundled pip locally.
& $VenvPython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare pip. Reinstall the standard 64-bit Python 3.12 distribution.' }

$Extras = if ($Dev) { 'ocr,dev' } else { 'ocr' }
# A constrained local build backend also avoids a second, unbounded resolver
# inside pip's isolated build directory.
& $VenvPython -m pip install --index-url https://pypi.org/simple --constraint $Constraints setuptools
if ($LASTEXITCODE -ne 0) { throw 'Cannot install the constrained build backend. Check the network and try again.' }
& $VenvPython -m pip install --index-url https://pypi.org/simple --constraint $Constraints --only-binary=:all: --prefix $ExtendedVenvPrefix PySide6-Essentials
if ($LASTEXITCODE -ne 0) { throw 'Cannot install the Qt runtime. Check the error above and try again.' }
& $VenvPython -m pip install --index-url https://pypi.org/simple --no-build-isolation --constraint $Constraints -e "${ProjectRoot}[$Extras]"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. See the error above and run setup again.' }
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed. See pip check above.' }
& $VenvPython -B (Join-Path $PSScriptRoot 'launch_gui.py') --check
if ($LASTEXITCODE -ne 0) { throw 'The windowless desktop launcher could not be prepared.' }
Write-Host 'Setup complete. Run the desktop launcher in the project root.'
Write-Host 'RapidOCR may download its default models on first use; translation APIs are optional.'
