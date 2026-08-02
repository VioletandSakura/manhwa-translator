@echo off
setlocal
set "TEMP=D:\Chatgpt\99_Temp\manga-translator-personal"
set "TMP=D:\Chatgpt\99_Temp\manga-translator-personal"
set "HF_HOME=D:\Chatgpt\99_Temp\manga-translator-personal\huggingface"
set "PIP_CACHE_DIR=D:\Chatgpt\99_Temp\pip-cache"
cd /d "D:\Chatgpt\01_Projects\manga-translator-personal"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 尚未安装运行环境，请先运行 scripts\setup.ps1
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "run.py"
endlocal

