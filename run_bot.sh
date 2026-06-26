#!/bin/bash
cd /root/Opencode
exec python3 meme_bot.py >> logs/bot.log 2>&1
