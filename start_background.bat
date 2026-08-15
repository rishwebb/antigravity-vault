@echo off
title Antigravity Background Launcher
cd /d "%~dp0"
echo Starting Antigravity Analytics Dashboard in background...
cscript //nologo start_background.vbs
echo.
echo ===================================================================
echo   ✓ Daemon running silently in background!
echo   ✓ Web Dashboard available at: http://localhost:4848
echo   ✓ To stop background server, run: stop_background.bat
echo ===================================================================
timeout /t 3 >nul
