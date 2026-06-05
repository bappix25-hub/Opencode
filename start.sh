#!/bin/bash
# Auto-restart script for Opencode Solana Meme Bot
# Usage:
#   bash start.sh                 # uses .env
#   bash start.sh v2              # uses .env.v2 (Termux parallel bot)
# WiFi reconnect → bot auto-starts

cd "$(dirname "$0")"

ENV_FILE=".env"
if [ "$1" = "v2" ] || [ -n "$BOT_INSTANCE" ]; then
    if [ -f .env.v2 ]; then
        ENV_FILE=".env.v2"
    fi
fi

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

LOG_FILE="${LOG_FILE:-./bot.log}"
DATA_FILE="${DATA_FILE:-./bot_data.json}"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null

if [ -n "$GITHUB_PAT" ] && [ -d .git ]; then
    REMOTE_URL="https://${GITHUB_USER:-bappix25-hub}:${GITHUB_PAT}@github.com/${GITHUB_USER:-bappix25-hub}/${GITHUB_REPO:-Opencode}.git"
    if git remote get-url origin >/dev/null 2>&1; then
        git remote set-url origin "$REMOTE_URL" 2>/dev/null
    else
        git remote add origin "$REMOTE_URL" 2>/dev/null
    fi
fi

echo "🤖 Opencode Bot Starting..."
echo "📅 $(date)"
echo "📂 Data: $DATA_FILE"
echo "📝 Log:  $LOG_FILE"
echo "🔧 Env:  $ENV_FILE"
if [ -n "$GITHUB_PAT" ]; then
    echo "🔑 GitHub: PAT configured (auto-push enabled)"
else
    echo "🔓 GitHub: public clone (push disabled — set GITHUB_PAT)"
fi
echo "━━━━━━━━━━━━━━━━━━━━"

while true; do
    echo "🚀 Launching at $(date)" >> "$LOG_FILE"
    python3 meme_bot.py >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    echo "❌ Exited code=$EXIT_CODE at $(date), restart in 10s" >> "$LOG_FILE"
    sleep 10
done
