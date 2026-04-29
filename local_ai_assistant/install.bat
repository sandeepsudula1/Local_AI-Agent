@echo off
echo ============================================
echo   Setting up Local AI Assistant...
echo ============================================

echo Creating virtual environment...
python -m venv venv311

echo Activating environment...
call venv311\Scripts\activate

echo Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo ============================================
echo   Setup completed successfully!
echo ============================================

pause