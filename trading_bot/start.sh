#!/bin/bash

cd "$(dirname "$0")"

if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

check_internet() {
    curl -s --max-time 5 --connect-timeout 3 https://frontend-api-v3.pump.fun/coins?limit=1 > /dev/null 2>&1
}

get_pid() {
    pgrep -f "trading_bot/main.py" 2>/dev/null | head -1
}

while true; do
    if ! check_internet(); then
        echo "$(date) - ⚠️ No internet, waiting 10s..."
        sleep 10
        continue
    fi

    PID=$(get_pid)
    if [ -n "$PID" ]; then
        echo "$(date) - Bot already running (PID: $PID), waiting 30s..."
        sleep 30
        continue
    fi

    echo "$(date) - ✅ Starting trading bot..."
    python3 main.py &
    BOT_PID=$!
    echo "$(date) - Bot started (PID: $BOT_PID)"

    wait $BOT_PID 2>/dev/null
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "$(date) - Bot exited cleanly, restarting in 5s..."
    else
        echo "$(date) - Bot crashed (exit: $EXIT_CODE), restarting in 10s..."
        sleep 5
    fi
    sleep 5
done
