@echo off
setlocal
cd /d "%~dp0"
python -c "import tkinter, pynput, pyperclip, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing recorder dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Setup failed. Install Python 3.10 or newer with the Add to PATH option.
        pause
        exit /b 1
    )
)
start "" pythonw.exe "%~dp0recorder.py"
