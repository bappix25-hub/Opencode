#!/bin/bash
# Auto-restart script for Opencode Solana Meme Bot
# Usage: bash start.sh
# WiFi reconnect → bot auto-starts

cd "$(dirname "$0")"

echo "🤖 Opencode Bot Starting..."
echo "📅 $(date)"
echo "━━━━━━━━━━━━━━━━━━━━"

while true; do
    echo "🚀 Launching bot at $(date)"
    python3 meme_bot.py
    EXIT_CODE=$?
    echo "❌ Bot exited with code $EXIT_CODE at $(date)"
    echo "🔄 Restarting in 10 seconds..."
    sleep 10
done
