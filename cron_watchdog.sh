#!/bin/null
# Cron watchdog — runs every minute, ensures daemon is alive
cd /root/Opencode
if ! cat .bot_daemon.pid 2>/dev/null | xargs kill -0 2>/dev/null; then
    echo "[$(date)] Watchdog: daemon dead → restarting" >> bot.log
    bash daemon.sh start >> bot.log 2>&1
fi
