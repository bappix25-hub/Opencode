#!/bin/bash
cd /root/Opencode

# Check if bot is actually alive (not zombie)
ALIVE=0
for PID in $(pgrep -f "python3 meme_bot"); do
    if [ -d "/proc/$PID/fd" ] && [ -r "/proc/$PID/fd/1" ]; then
        ALIVE=1
        break
    fi
done

# Check if log is recent (within 3 min)
LAST_LOG=$(stat -c %Y logs/bot.log 2>/dev/null || echo 0)
NOW=$(date +%s)
AGE=$((NOW - LAST_LOG))

if [ "$ALIVE" -eq 1 ] && [ "$AGE" -lt 180 ]; then
    exit 0  # Bot is fine
fi

# Kill all zombies
pkill -9 -f "python3 meme_bot" 2>/dev/null
sleep 2

# Clean up Maestro session locks (prevents "database is locked")
rm -f maestro_session* 2>/dev/null

# Start fresh
nohup python3 -u meme_bot.py >> logs/bot.log 2>&1 &
disown
echo "$(date) Restarted bot (alive=$ALIVE log_age=${AGE}s)"