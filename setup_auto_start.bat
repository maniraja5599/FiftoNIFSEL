@echo off
title Setup FIFTO Auto-Start
echo ========================================
echo FIFTO AI Trading - Auto-Start Setup
echo ========================================
echo.

cd /d "%~dp0"

echo Creating Windows Scheduled Task...
echo This will run FIFTO server silently when you log in.
echo.

schtasks /create /tn "FIFTO AI Trading Server" /tr "wscript.exe \"e:\Projects\NIFTY Claude Setup\start_silent.vbs\"" /sc onlogon /rl limited /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS! FIFTO will now auto-start when you log in.
    echo The server runs silently in background on port 8080.
    echo.
    echo To access dashboard: http://localhost:8080
    echo.
    echo To manage this task:
    echo   - Open Task Scheduler
    echo   - Find "FIFTO AI Trading Server"
    echo   - Disable/Delete to stop auto-start
    echo.
) else (
    echo.
    echo ERROR! Failed to create scheduled task.
    echo Please run this file as Administrator.
    echo.
)

pause
