#!/bin/bash
set -e

cd "$(dirname "$0")"

mkdir -p logs

PID_FILE="logs/server.pid"
LOG_FILE="logs/server_$(date +%Y%m%d).log"

# If already running, do nothing
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Server is already running (PID $(cat "$PID_FILE"))"
    exit 0
fi

echo "Starting server..."
nohup env PYTHONPATH=src uv run uvicorn manufacture_jis.web.app:app --host 0.0.0.0 --port 8502 \
    >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "Server started (PID $(cat "$PID_FILE"))"
echo "Logs: $LOG_FILE"
