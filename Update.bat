@echo off
cd /d "%~dp0"
title Raid Bot - Update
echo.
echo  RAID BOT - UPDATE
echo  Checks GitHub for a newer version and installs it.
echo  Your settings and data are kept.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
echo.
pause
