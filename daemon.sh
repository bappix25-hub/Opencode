#!/bin/bash
# 24/7 Bot Daemon — survives crashes, internet loss, reboots
# Usage: bash daemon.sh [start|stop|status]

cd "$(dirname "$0")"
PID_FILE=".bot_daemon.pid"
BOT_LOG="bot.log"
ENV_FILE=".env"

source_env() {
    if [ -f "$ENV_FILE" ]; then
        set -a; source "$ENV_FILE"; set +a
    fi
}

check_internet() {
    curl -fsS -m 5 -o /dev/null "https://api.telegram.org" 2>/dev/null
    return $?
}

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        kill -0 "$pid" 2>/dev/null && return 0
    fi
    return 1
}

start() {
    if is_running; then
        echo "✅ Already running (PID $(cat $PID_FILE))"
        return 0
    fi

    source_env
    nohup bash -c '
        cd "'"$PWD"'"
        while true; do
            if curl -fsS -m 5 -o /dev/null "https://api.telegram.org" 2>/dev/null; then
                echo "🚀 Launching at $(date)" >> "'"$BOT_LOG"'"
                python3 meme_bot.py >> "'"$BOT_LOG"'" 2>&1
                EXIT_CODE=$?
                echo "❌ Exited code=$EXIT_CODE at $(date), restart in 10s" >> "'"$BOT_LOG"'"
                sleep 10
            else
                echo "⏳ No internet at $(date), retry in 30s" >> "'"$BOT_LOG"'"
                sleep 30
            fi
        done
    ' &>/dev/null &

    echo $! > "$PID_FILE"
    echo "🤖 Bot started (PID $!)"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        kill -- -"$pid" 2>/dev/null
        kill "$pid" 2>/dev/null
        pkill -f "meme_bot.py" 2>/dev/null
        rm -f "$PID_FILE"
        echo "🛑 Bot stopped"
    else
        pkill -f "meme_bot.py" 2>/dev/null
        pkill -f "daemon.sh" 2>/dev/null
        rm -f "$PID_FILE"
        echo "🛑 Bot stopped (cleanup)"
    fi
}

status() {
    if is_running; then
        echo "✅ Running (PID $(cat $PID_FILE))"
    else
        echo "❌ Not running"
    fi
    echo "--- Last 5 log lines ---"
    tail -5 "$BOT_LOG" 2>/dev/null
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    *)      echo "Usage: $0 {start|stop|status}" ;;
esac
