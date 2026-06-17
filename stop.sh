#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PID_FILE="logs/server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Server is not running"
    exit 0
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "Server is not running"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping server (PID $PID)..."
kill "$PID"

for _ in {1..20}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Server stopped"
        exit 0
    fi
    sleep 0.5
done

echo "Server did not exit in time, forcing stop..."
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Server stopped"
