@echo off
cd /d "%~dp0"
title Raid Bot - First Time Setup
echo.
echo  RAID BOT - FIRST TIME SETUP
echo  Just follow the prompts. This takes a few minutes.
echo.

rem ---- 1. find Python (python, py launcher, or common install folders)
set "PY="
where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    for %%D in ("%LocalAppData%\Programs\Python\Python313" "%LocalAppData%\Programs\Python\Python312" "%LocalAppData%\Programs\Python\Python311" "%LocalAppData%\Programs\Python\Python310") do (
        if not defined PY if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
    )
)

rem ---- 2. no Python? install it automatically with winget
rem (these lines are deliberately not inside ( ) blocks: %WG% must be read when the line runs, not when the block is parsed)
if defined PY goto :have_python
echo Python isn't installed. Installing it now, this takes a minute...
set "WG="
where winget >nul 2>nul && set "WG=winget"
if not defined WG if exist "%LocalAppData%\Microsoft\WindowsApps\winget.exe" set "WG=%LocalAppData%\Microsoft\WindowsApps\winget.exe"
if defined WG "%WG%" install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
:have_python
if not defined PY (
    echo.
    echo I couldn't install Python automatically.
    echo A download page is opening. Install it and TICK "Add python.exe to PATH",
    echo then double-click SETUP.bat again.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ---- 3. private environment + dependencies
if not exist ".venv\Scripts\python.exe" (
    echo Preparing the bot ^(first time only, about a minute^)...
    %PY% -m venv .venv
    if errorlevel 1 ( echo Couldn't create the environment. & pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Couldn't download the bot's components. Check your internet connection and run SETUP.bat again.
    pause
    exit /b 1
)

rem ---- 4. guided questions, writes .env
if exist ".setup_ok" del ".setup_ok"
".venv\Scripts\python.exe" setup.py
if not exist ".setup_ok" (
    echo.
    echo Setup didn't finish. Double-click SETUP.bat to try again.
    pause
    exit /b 1
)

rem ---- 5. delete the setup files, then launch the bot
del ".setup_ok"
rem (in a git clone these files are tracked, so keep them)
if exist ".git" ( call "%~dp0start.bat" & exit /b )
(goto) 2>nul & del "%~dp0setup.py" & del "%~f0" & call "%~dp0start.bat"
