#!/bin/bash
# Auto-restart script for Opencode Solana Meme Bot
# Usage:
#   bash start.sh                 # uses .env
#   bash start.sh v2              # uses .env.v2 (Termux parallel bot)
# WiFi reconnect → bot auto-starts (waits for connectivity first)

cd "$(dirname "$0")"

# Auto-update from GitHub before starting
if [ -d .git ]; then
    echo "📥 Updating from GitHub..."
    git pull origin main 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "✅ Updated to latest version"
    else
        echo "⚠️ Update failed (no internet or conflict), continuing..."
    fi
fi

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

check_internet() {
    local target="${1:-https://api.telegram.org}"
    curl -fsS -m 5 -o /dev/null "$target" 2>/dev/null
    return $?
}

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

INTERNET_WAIT=15
ATTEMPT=0
while true; do
    if check_internet "https://api.telegram.org"; then
        if [ $ATTEMPT -gt 0 ]; then
            echo "🌐 Internet back at $(date) (after $ATTEMPT retries)" >> "$LOG_FILE"
        fi
        ATTEMPT=0
        echo "🚀 Launching at $(date)" >> "$LOG_FILE"
        python3 meme_bot.py >> "$LOG_FILE" 2>&1
        EXIT_CODE=$?
        echo "❌ Exited code=$EXIT_CODE at $(date), restart in 10s" >> "$LOG_FILE"
        sleep 10
    else
        ATTEMPT=$((ATTEMPT + 1))
        if [ $((ATTEMPT % 4)) -eq 1 ]; then
            echo "⏳ No internet (try $ATTEMPT) — waiting ${INTERNET_WAIT}s" >> "$LOG_FILE"
        fi
        sleep $INTERNET_WAIT
    fi
done

