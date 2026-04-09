@echo off
title Enable FIFTO Auto-Start
echo Enabling FIFTO AI Trading auto-start...
echo.

schtasks /change /tn "FIFTO AI Trading Server" /enable

if %errorlevel% equ 0 (
    echo SUCCESS! FIFTO will auto-start on login.
) else (
    echo Task not found. Please run setup_auto_start.bat first.
)

pause
