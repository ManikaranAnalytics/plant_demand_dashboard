@echo off
title Plant Demand Reminder Scheduler
cd /d "%~dp0"
cd ..
echo Checking for Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python not found in Path.
    pause
    exit /b
)
echo Starting Persistent Scheduler (IST Time)...
python scripts\scheduler.py
pause
