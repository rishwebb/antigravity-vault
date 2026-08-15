#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/antigravity_server.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    kill -9 $PID 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Stopped server process PID $PID."
fi

# Fallback check port 4848
fuser -k 4848/tcp 2>/dev/null || true
echo "Antigravity Analytics server stopped."
