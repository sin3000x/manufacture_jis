#!/usr/bin/env bash

cd "$(dirname "$0")"

mkdir -p logs

PID_FILE="logs/server.pid"
LOG_FILE="logs/server_$(date +%Y%m%d).log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Server is already running (PID $PID)"
        exit 0
    fi
fi

echo "Starting server..."
nohup env PYTHONPATH=src uv run uvicorn manufacture_jis.web.app:app --host 0.0.0.0 --port 8502 \
    >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "Server started (PID $(cat "$PID_FILE"))"
echo "Logs: $LOG_FILE"
