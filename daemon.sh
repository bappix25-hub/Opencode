#!/bin/bash
# Persistent bot daemon - restarts bot on any exit
cd "$(dirname "$0")"
touch /tmp/bot_daemon_$(id -u).pid
echo $$ > /tmp/bot_daemon_$(id -u).pid
trap "rm -f /tmp/bot_daemon_$(id -u).pid; exit" TERM INT

while true; do
    python3 meme_bot.py 2>>logs/bot.log
    EXIT=$?
    echo "[$(date)] Bot exited code=$EXIT, restarting in 3s..." >> logs/bot.log
    sleep 3
done
