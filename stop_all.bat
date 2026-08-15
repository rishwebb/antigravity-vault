@echo off
title Stop Antigravity Analytics & Services
echo Stopping all Antigravity Analytics, Watcher, and Tunnel services...

:: Kill processes on port 4848
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :4848') do (
    if not "%%a"=="" (
        taskkill /F /PID %%a >nul 2>&1
    )
)

:: Terminate any cloudflared tunnels
taskkill /F /IM cloudflared.exe >nul 2>&1

echo All services stopped cleanly.
timeout /t 2 >nul
