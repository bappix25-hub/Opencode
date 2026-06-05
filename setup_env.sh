#!/bin/bash
# Opencode Bot — Interactive .env Setup
# Usage: bash setup_env.sh
# Result: creates .env with real tokens (chmod 600, gitignored)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔑 Opencode Bot — Token Setup"
echo "━━━━━━━━━━━━━━━━━━━━"
echo "আপনার credentials লাগবে (সব silent mode এ read হবে):"
echo ""

if [ ! -t 0 ]; then
    echo "❌ Interactive terminal নেই। Manual setup: copy .env.example থেকে .env তৈরি করুন।"
    exit 1
fi

read -p "🤖 Telegram Bot Token: " -r BOT_TOKEN
while [ -z "$BOT_TOKEN" ]; do
    echo "   Token empty, আবার দিন:"
    read -p "🤖 Telegram Bot Token: " -r BOT_TOKEN
done

read -p "🆔 Telegram Chat ID [5461546008]: " -r CHAT_ID
CHAT_ID="${CHAT_ID:-5461546008}"
while ! [[ "$CHAT_ID" =~ ^[0-9-]+$ ]]; do
    echo "   Chat ID numeric হতে হবে, আবার দিন:"
    read -p "🆔 Telegram Chat ID: " -r CHAT_ID
done

read -p "🔑 Helius API Key: " -r HELIUS_KEY
while [ -z "$HELIUS_KEY" ]; do
    echo "   Key empty, আবার দিন:"
    read -p "🔑 Helius API Key: " -r HELIUS_KEY
done

echo ""
echo "🔑 GitHub PAT (optional — auto data sync এর জন্য।"
read -p "   Enter দিয়ে skip করতে পারেন, পরে .env এ add করবেন): " -r GITHUB_PAT
GITHUB_PAT="${GITHUB_PAT:-}"
GITHUB_USER="${GITHUB_USER:-bappix25-hub}"
GITHUB_REPO="${GITHUB_REPO:-Opencode}"

cat > .env << EOF
BOT_TOKEN=$BOT_TOKEN
CHAT_ID=$CHAT_ID
HELIUS_API_KEY=$HELIUS_KEY

TWITTER_BEARER_TOKEN=

GITHUB_PAT=$GITHUB_PAT
GITHUB_USER=$GITHUB_USER
GITHUB_REPO=$GITHUB_REPO

PUMPPORTAL_WS=wss://pumpportal.fun/api/data
RUGCHECK_URL=https://api.rugcheck.xyz/v1
DATA_FILE=./bot_data.json
LOG_FILE=./bot.log

PUMP_MULTIPLIER=3.0
AI_THRESHOLD=0.50
MIN_LIQUIDITY=2000
MIN_VOLUME=300
MIN_MCAP=1000
MAX_MCAP=2000000

SCAN_INTERVAL=120
HISTORY_SCAN_INTERVAL=3600
GITHUB_SYNC_INTERVAL=21600
CLEANUP_INTERVAL=3600
BACKTEST_INTERVAL=604800

DEXSCREENER_MAX_RETRIES=3
DEXSCREENER_BASE_DELAY=1.0

ENABLE_PRE_MIGRATION=true
ENABLE_HISTORY_SCAN=true
ENABLE_GITHUB_SYNC=true
ENABLE_SOCIAL_SIGNALS=true
ENABLE_AUTO_VERIFY=true

SIGNAL_MIN_THRESHOLD=0.60
GOLDEN_MIN_COUNT=5
BLACKLIST_MAX_FAILS=3
EOF

chmod 600 .env
echo ""
echo "✅ .env তৈরি হয়েছে (permission 600, owner-only)"
echo "📁 Location: $SCRIPT_DIR/.env"
echo "🛡️  Gitignored — কখনো GitHub এ যাবে না"
echo ""
echo "🚀 Bot চালু করুন: bash start.sh"
