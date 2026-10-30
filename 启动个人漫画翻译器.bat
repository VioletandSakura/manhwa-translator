@echo off
setlocal
set "TEMP=%~dp0.cache\tmp"
set "TMP=%TEMP%"
set "HF_HOME=%TEMP%\huggingface"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
cd /d "%~dp0"
if errorlevel 1 goto path_error
if not exist "%~dp0.venv\Scripts\python.exe" goto python_error
if not exist "%~dp0run.py" goto app_error
if defined MANGA_TRANSLATOR_LAUNCH_CHECK goto check_ok
start "" "%SystemRoot%\System32\wscript.exe" "%~dpn0.vbs"
if errorlevel 1 goto launch_error
exit /b 0

:check_ok
"%~dp0.venv\Scripts\python.exe" -B "%~dp0scripts\launch_gui.py" --check
exit /b %errorlevel%

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
