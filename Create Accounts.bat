@echo off
cd /d "%~dp0"
python create_accounts.py --start 22 --end 100
pause
