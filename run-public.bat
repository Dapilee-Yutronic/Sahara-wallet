@echo off
REM Binds to all network interfaces so friends, tunnels, and phones can reach the app.
cd /d "%~dp0"
python run.py
pause
