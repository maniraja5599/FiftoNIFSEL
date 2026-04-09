@echo off
title Disable FIFTO Auto-Start
echo Disabling FIFTO AI Trading auto-start...
echo.

schtasks /change /tn "FIFTO AI Trading Server" /disable

if %errorlevel% equ 0 (
    echo SUCCESS! FIFTO will NOT auto-start on login.
    echo.
    echo To re-enable: Run setup_auto_start.bat
) else (
    echo Task not found or error occurred.
)

pause
