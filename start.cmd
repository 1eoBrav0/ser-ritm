@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 server.py
    goto finish
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" server.py
    goto finish
)
where python >nul 2>&1
if not errorlevel 1 (
    python server.py
    goto finish
)
echo Для запуска нужен Python 3.11 или новее: https://www.python.org/downloads/
:finish
pause
