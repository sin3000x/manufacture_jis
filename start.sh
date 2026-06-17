#!/usr/bin/env bash

cd "$(dirname "$0")"

mkdir -p logs

PID_FILE="logs/server.pid"
LOG_FILE="logs/server.log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Server is already running (PID $PID)"
        exit 0
    fi
fi

echo "Starting server..."
nohup env PYTHONPATH=src uv run python -m manufacture_jis.web.run_server >/dev/null 2>&1 &

echo $! > "$PID_FILE"
echo "Server started (PID $(cat "$PID_FILE"))"
echo "Logs: $LOG_FILE (rotates daily at midnight)"
