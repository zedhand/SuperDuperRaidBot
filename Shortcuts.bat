@echo off
cd /d "%~dp0"
title Raid Bot - Shortcuts
echo.
echo  RAID BOT - SHORTCUTS
echo  Switch the desktop shortcut and "start with Windows" on or off.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" checks.py shortcuts
) else (
    python checks.py shortcuts
)
echo.
pause
