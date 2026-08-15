@echo off
title Antigravity Enterprise Cloud Vault Launcher
cd /d "%~dp0"
echo ===================================================================
echo   🚀 Starting Antigravity Enterprise Analytics & Cloud Vault
echo ===================================================================
cscript //nologo start_all_background.vbs
echo.
echo   [+] Analytics Server & Database Vault: Active
echo   [+] Live Forex Dynamic Engine: Active (USD/INR Auto-Update)
echo   [+] Background Watcher: Active (0.5s debounced telemetry listener)
echo   [+] Daily Automated Snapshots: Active (~/.antigravity_analytics_vault/)
echo   [+] Free Cloudflare HTTPS & LAN Tunnel: Active
echo.
echo   ✨ Local Web Dashboard: http://localhost:4848
echo   🔒 Security PIN Code:   4848
echo   🛑 To stop all services: run stop_all.bat
echo ===================================================================
timeout /t 3 >nul
