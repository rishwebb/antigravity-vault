@echo off
title Antigravity Token & Cost Analytics Dashboard
echo ===================================================================
echo   🚀 Antigravity Multi-Account Token & Cost Analytics Dashboard
echo ===================================================================
echo.

:: Check python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.8+ and add it to your system PATH.
    pause
    exit /b 1
)

:: Optional install watchdog
echo [1/3] Checking optional dependencies...
pip install -q -r requirements.txt >nul 2>&1

:: Initialize Database & Initial Ingestion
echo [2/3] Initializing local SQLite database and syncing telemetry...
python -c "import db, telemetry_parser; db.init_db(); telemetry_parser.discover_and_sync_all();"

:: Launch Server
echo [3/3] Starting Local Dashboard Server on http://localhost:4848 ...
echo.
echo ===================================================================
echo   ✨ Dashboard URL: http://localhost:4848
echo   ✨ Telemetry Daemon: Active (0.5s debounced file watcher)
echo   ✨ Models: Gemini 3.5 Pro, 3.6 Flash, 3.7 Flash + Thinking Tokens
echo ===================================================================
echo.
python server.py
pause
