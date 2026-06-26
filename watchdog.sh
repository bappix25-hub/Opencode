#!/bin/bash
cd /root/Opencode

# === WATCHDOG: Restart bot if stuck, dead, or internet was down ===

NOW=$(date +%s)

# 1. Check internet
INTERNET_OK=0
if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
    INTERNET_OK=1
fi

# 2. Check bot process alive
ALIVE=0
for PID in $(pgrep -f "python3 meme_bot"); do
    if [ -d "/proc/$PID/fd" ] && [ -r "/proc/$PID/fd/1" ]; then
        ALIVE=1
        break
    fi
done

# 3. Check log freshness (within 3 min = OK)
LAST_LOG=$(stat -c %Y logs/bot.log 2>/dev/null || echo 0)
AGE=$((NOW - LAST_LOG))

# 4. Check heartbeat (bot writes timestamp to /tmp every 30s)
HEARTBEAT_AGE=999
if [ -f /tmp/meme_bot_heartbeat ]; then
    HB=$(cat /tmp/meme_bot_heartbeat 2>/dev/null || echo 0)
    HEARTBEAT_AGE=$((NOW - HB))
fi

# DECISION
SHOULD_RESTART=0
REASON=""

if [ "$ALIVE" -eq 0 ]; then
    SHOULD_RESTART=1
    REASON="process_dead"
elif [ "$AGE" -gt 180 ]; then
    SHOULD_RESTART=1
    REASON="log_stale_${AGE}s"
elif [ "$HEARTBEAT_AGE" -gt 90 ]; then
    SHOULD_RESTART=1
    REASON="no_heartbeat_${HEARTBEAT_AGE}s"
elif [ "$INTERNET_OK" -eq 0 ] && [ "$AGE" -gt 300 ]; then
    SHOULD_RESTART=1
    REASON="internet_down_stale_log"
fi

if [ "$SHOULD_RESTART" -eq 0 ]; then
    exit 0
fi

echo "$(date) RESTART: $REASON alive=$ALIVE log_age=${AGE}s hb=${HEARTBEAT_AGE}s net=$INTERNET_OK"

# Kill zombies
pkill -9 -f "python3 meme_bot" 2>/dev/null
sleep 3

# Clean session locks
rm -f maestro_session* 2>/dev/null

# Wait for internet if down
if [ "$INTERNET_OK" -eq 0 ]; then
    echo "$(date) Waiting for internet..."
    for i in $(seq 1 30); do
        if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
            echo "$(date) Internet back!"
            sleep 5
            break
        fi
        sleep 10
    done
fi

# Start bot
nohup python3 -u meme_bot.py >> logs/bot.log 2>&1 &
disown
echo "$(date) Bot started PID=$!"