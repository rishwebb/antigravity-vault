#!/usr/bin/env bash
set -e

echo "==================================================================="
echo "  🚀 Antigravity Multi-Account Token & Cost Analytics Dashboard"
echo "==================================================================="
echo ""

# Find python3 or python
if command -v python3 &>/dev/null; then
    PY_BIN="python3"
elif command -v python &>/dev/null; then
    PY_BIN="python"
else
    echo "[ERROR] Python 3 was not found in PATH!"
    exit 1
fi

echo "[1/3] Checking optional dependencies..."
$PY_BIN -m pip install -q -r requirements.txt 2>/dev/null || true

echo "[2/3] Initializing local database and syncing telemetry..."
$PY_BIN -c "import db, telemetry_parser; db.init_db(); telemetry_parser.discover_and_sync_all();"

echo "[3/3] Starting Local Dashboard Server on http://localhost:4848 ..."
echo ""
echo "==================================================================="
echo "  ✨ Dashboard URL: http://localhost:4848"
echo "  ✨ Telemetry Daemon: Active (0.5s debounced file watcher)"
echo "  ✨ Models: Gemini 3.5 Pro, 3.6 Flash, 3.7 Flash + Thinking Tokens"
echo "==================================================================="
echo ""

$PY_BIN server.py
