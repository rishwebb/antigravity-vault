#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 &>/dev/null; then
    PY_BIN="python3"
else
    PY_BIN="python"
fi

nohup $PY_BIN server.py > /dev/null 2>&1 &
PID=$!
echo $PID > antigravity_server.pid

echo "==================================================================="
echo "  ✓ Daemon running silently in background! (PID: $PID)"
echo "  ✓ Web Dashboard available at: http://localhost:4848"
echo "  ✓ To stop background server, run: ./stop_background.sh"
echo "==================================================================="
