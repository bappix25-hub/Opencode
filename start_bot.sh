#!/bin/bash
cd /root/Opencode
pkill -f meme_bot.py 2>/dev/null
sleep 2
python3 meme_bot.py >> bot.log 2>&1 &
echo "Bot started with PID: $!"
