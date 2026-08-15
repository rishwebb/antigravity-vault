@echo off
title Stop Antigravity Background Server
echo Stopping Antigravity server running on port 4848...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :4848') do (
    if not "%%a"=="" (
        taskkill /F /PID %%a >nul 2>&1
        echo Killed process PID %%a listening on port 4848.
    )
)
echo Server stopped.
timeout /t 2 >nul
