@echo off
cd /d "%~dp0"
title Raid Bot

rem First run: if SETUP.bat is still here and no token is saved yet, run it (it deletes itself when done).
rem If a token is already saved (e.g. copied from an older install), setup isn't needed, so tidy it away
rem (but never inside a git clone, where those files are tracked).
if exist "SETUP.bat" (
    findstr /r /c:"^DISCORD_TOKEN=..*" ".env" >nul 2>nul
    if errorlevel 1 (
        call "%~dp0SETUP.bat"
        exit /b
    )
    if not exist ".git" (
        del "setup.py" >nul 2>nul
        del "SETUP.bat" >nul 2>nul
    )
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed.
    echo Download it from https://www.python.org/downloads/
    echo During install, tick the box "Add python.exe to PATH". Then run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: creating environment...
    python -m venv .venv
)

echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

echo Starting the bot. Leave this window open. Close it to stop the bot.
".venv\Scripts\python.exe" bot.py
echo.
echo The bot stopped. Read any message above for the reason.
pause
