@echo off
cd /d "%~dp0"
:loop
python main.py
echo bot stopped, restarting in 10s...
timeout /t 10 /nobreak >nul
goto loop
