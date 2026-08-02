$ErrorActionPreference = 'Stop'

$ProjectRoot = 'D:\Chatgpt\01_Projects\manga-translator-personal'
$TempRoot = 'D:\Chatgpt\99_Temp\manga-translator-personal'
$PipCache = 'D:\Chatgpt\99_Temp\pip-cache'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $ProjectRoot, $TempRoot, $PipCache | Out-Null
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = $PipCache
$env:HF_HOME = Join-Path $TempRoot 'huggingface'

if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
}

& $VenvPython -m pip install -e "$ProjectRoot[ocr]"
Write-Host 'Setup complete. Run the desktop launcher in the project root.'
