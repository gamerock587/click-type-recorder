@echo off
setlocal
cd /d "%~dp0"
python -c "import tkinter, cv2, numpy, pytesseract, PIL, pynput" >nul 2>&1
if errorlevel 1 (
    echo Installing Smart Bot dependencies...
    python -m pip install -r smart_bot_requirements.txt
    if errorlevel 1 (
        echo Dependency setup failed.
        pause
        exit /b 1
    )
)
start "" pythonw.exe "%~dp0smart_bot_gui.py"
