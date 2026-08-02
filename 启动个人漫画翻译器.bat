@echo off
setlocal
set "TEMP=D:\Chatgpt\99_Temp\manga-translator-personal"
set "TMP=D:\Chatgpt\99_Temp\manga-translator-personal"
set "HF_HOME=D:\Chatgpt\99_Temp\manga-translator-personal\huggingface"
set "PIP_CACHE_DIR=D:\Chatgpt\99_Temp\pip-cache"
cd /d "%~dp0"
if errorlevel 1 goto path_error
if not exist "%~dp0.venv\Scripts\pythonw.exe" goto python_error
if not exist "%~dp0run.py" goto app_error
if defined MANGA_TRANSLATOR_LAUNCH_CHECK goto check_ok
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run.py"
if errorlevel 1 goto launch_error
exit /b 0

:check_ok
echo Launcher check passed.
exit /b 0

:path_error
echo [ERROR] Cannot open the application folder.
goto failed

:python_error
echo [ERROR] Python environment is missing. Run scripts\setup.ps1 first.
goto failed

:app_error
echo [ERROR] run.py is missing.
goto failed

:launch_error
echo [ERROR] The application could not be started.

:failed
pause
exit /b 1
