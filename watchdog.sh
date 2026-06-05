#!/bin/bash
# Watchdog: restart bot if crashed or internet came back
# Runs via cron every 2 minutes

BOT_DIR="/root/Opencode"
LOG_FILE="$BOT_DIR/bot.log"
PID_FILE="$BOT_DIR/.bot.pid"

check_internet() {
    curl -fsS -m 5 -o /dev/null "https://api.telegram.org" 2>/dev/null
    return $?
}

# Check if bot process is alive
bot_alive() {
    if pgrep -f "meme_bot.py" > /dev/null 2>&1; then
        return 0
    fi
    # Also check if start.sh loop is running
    if pgrep -f "start.sh" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

if bot_alive; then
    exit 0
fi

if check_internet; then
    echo "[$(date)] Watchdog: bot dead + internet OK → restarting" >> "$LOG_FILE"
    cd "$BOT_DIR"
    nohup bash start.sh &>/dev/null &
else
    echo "[$(date)] Watchdog: bot dead + no internet → waiting" >> "$LOG_FILE"
fi
