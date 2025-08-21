@echo off
title FiFTO OAuth Quick Start
echo ========================================
echo FiFTO OAuth Quick Start
echo ========================================
echo.
echo This script will:
echo 1. Start the OAuth server
echo 2. Open the main FiFTO application
echo.
echo After OAuth authentication, you'll be automatically
echo redirected back to the main application!
echo.
pause

echo Starting OAuth server in background...
start "FiFTO OAuth Server" cmd /k "python flattrade_oauth_server.py"

timeout /t 3 /nobreak > nul

echo Starting main FiFTO application...
python selling.py

echo.
echo Both applications started successfully!
echo - OAuth server: http://localhost:3001
echo - Main app: http://localhost:7860
echo.
pause
