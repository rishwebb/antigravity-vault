#!/usr/bin/env bash
echo "Stopping all Antigravity Analytics, Watcher, and Tunnel services..."

# Kill process on port 4848
fuser -k 4848/tcp 2>/dev/null || true

# Kill cloudflared
pkill -f cloudflared 2>/dev/null || true

echo "All services stopped cleanly."
