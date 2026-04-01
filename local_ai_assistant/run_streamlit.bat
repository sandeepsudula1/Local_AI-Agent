@echo off
:: run_streamlit.bat
:: Launch the Streamlit web UI for the Local AI Assistant
::
:: Run from the local_ai_assistant folder:
::   run_streamlit.bat

setlocal
cd /d "%~dp0"

set PYTHON=venv311\Scripts\python.exe
set STREAMLIT=venv311\Scripts\streamlit.exe

if not exist "%PYTHON%" (
    echo ERROR: venv311\Scripts\python.exe not found.
    echo Please create the virtual environment first.
    pause
    exit /b 1
)

if not exist "%STREAMLIT%" (
    echo Installing streamlit into venv311...
    "%PYTHON%" -m pip install streamlit --quiet
)

echo.
echo  ========================================
echo   Local AI Assistant - Streamlit Web UI
echo  ========================================
echo   Opening: http://localhost:8501
echo   Press Ctrl+C to stop.
echo.

:: Use python -m streamlit as fallback if exe was not created by pip
if exist "%STREAMLIT%" (
    "%STREAMLIT%" run streamlit_app.py ^
        --server.port 8501 ^
        --server.headless false ^
        --browser.gatherUsageStats false ^
        --theme.base dark ^
        --theme.primaryColor "#4f8bf9" ^
        --theme.backgroundColor "#0e1117" ^
        --theme.secondaryBackgroundColor "#1a1f2e"
) else (
    "%PYTHON%" -m streamlit run streamlit_app.py ^
        --server.port 8501 ^
        --server.headless false ^
        --browser.gatherUsageStats false ^
        --theme.base dark ^
        --theme.primaryColor "#4f8bf9" ^
        --theme.backgroundColor "#0e1117" ^
        --theme.secondaryBackgroundColor "#1a1f2e"
)
