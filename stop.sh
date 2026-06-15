#!/bin/bash
set -e

cd "$(dirname "$0")"

PID_FILE="logs/server.pid"

if [ ! -f "$PID_FILE" ] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Server is not running"
    rm -f "$PID_FILE"
    exit 0
fi

PID=$(cat "$PID_FILE")
echo "Stopping server (PID $PID)..."
kill "$PID"
rm -f "$PID_FILE"
echo "Server stopped"
