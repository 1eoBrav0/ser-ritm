@echo off
chcp 65001 >nul
powershell -NoProfile -File "%~dp0start-telegram.ps1"
pause
