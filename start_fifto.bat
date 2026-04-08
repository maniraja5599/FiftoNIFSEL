@echo off
title FIFTO AI Trading Server
cd /d "%~dp0"
echo Starting FIFTO AI Trading...
echo Dashboard will open at http://localhost:8080
timeout /t 2 /nobreak >nul
start "" "http://localhost:8080"
.venv\Scripts\python.exe dev_server.py
pause
