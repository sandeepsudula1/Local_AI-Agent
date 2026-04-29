@echo off
title Local AI Assistant
cd /d "%~dp0"

echo ============================================
echo   Local AI Assistant - Starting up...
echo ============================================
echo.

echo [1/3] Checking environment...
if not exist "venv311\Scripts\python.exe" (
    echo ERROR: venv311 not found!
    echo Make sure you are in the right folder.
    pause
    exit /b 1
)

echo [2/3] Stopping old instances...
taskkill /f /fi "WINDOWTITLE eq Local AI Assistant" >nul 2>&1

echo [3/3] Loading AI Assistant (please wait ~20 seconds)...
echo       Do NOT close this window.
echo.
echo Using Python:
venv311\Scripts\python.exe --version

venv311\Scripts\python.exe main.py

echo.
echo ============================================
echo   Assistant has stopped.
echo ============================================
pause
