#!/bin/bash
# Auto-restart script for Opencode Solana Meme Bot
# Usage:
#   bash start.sh                 # uses .env
#   bash start.sh v2              # uses .env.v2 (Termux parallel bot)
# WiFi reconnect → bot auto-starts (waits for connectivity first)

cd "$(dirname "$0")"

# Self-lock: prevent multiple start.sh for same instance
INSTANCE="${1:-main}"
START_LOCK="/tmp/start_sh_${INSTANCE}.lock"
if [ -f "$START_LOCK" ]; then
    OLD_PID=$(cat "$START_LOCK" 2>/dev/null)
    if [ -n "$OLD_PID" ] && [ -d "/proc/$OLD_PID" ]; then
        echo "❌ Another start.sh [instance=$INSTANCE] is already running (PID $OLD_PID). Exiting."
        exit 0
    fi
fi
echo $$ > "$START_LOCK"
trap "rm -f '$START_LOCK'" EXIT

# Auto-update from GitHub before starting
# Load env first to get DATA_FILE for smart-merge
if [ -f ".env" ]; then
    set -a; source ".env"; set +a
fi
if [ -f ".env.${1:-main}" ]; then
    set -a; source ".env.${1:-main}"; set +a
fi
INSTANCE_DATA="${DATA_FILE:-./bot_data.json}"

if [ -d .git ]; then
    echo "📥 Updating from GitHub..."
    mkdir -p "$HOME/.tmp_opencode"
    if [ -f "$INSTANCE_DATA" ]; then
        cp "$INSTANCE_DATA" "$HOME/.tmp_opencode/botdata_local_backup.json"
    fi

    git rebase --abort 2>/dev/null
    git merge --abort 2>/dev/null
    git fetch origin main 2>/dev/null

    git pull --no-rebase origin main 2>/dev/null
    PULL_EXIT=$?

    if [ $PULL_EXIT -ne 0 ]; then
        echo "⚠️ Pull failed, trying smart-merge..."
        git checkout origin/main -- "$INSTANCE_DATA" 2>/dev/null
        if [ -f "$HOME/.tmp_opencode/botdata_local_backup.json" ]; then
            REMOTE_TMP="$HOME/.tmp_opencode/remote_data_$$.json"
            cp "$INSTANCE_DATA" "$REMOTE_TMP"
            python3 << PYEOF
import json
try:
    local = json.load(open('$HOME/.tmp_opencode/botdata_local_backup.json'))
    remote = json.load(open('${REMOTE_TMP}'))
    def ml(a, b, k='address', c=100):
        s, o = set(), []
        for it in (a or []) + (b or []):
            kk = it.get(k)
            if kk and kk in s: continue
            if kk: s.add(kk)
            o.append(it)
        return o[:c]
    merged = dict(remote)
    merged['pump_patterns'] = ml(local.get('pump_patterns'), remote.get('pump_patterns'))
    merged['dump_patterns'] = ml(local.get('dump_patterns'), remote.get('dump_patterns'))
    merged['launch_patterns'] = ml(local.get('launch_patterns'), remote.get('launch_patterns'))
    merged['trained_addresses'] = {**(local.get('trained_addresses') or {}), **(remote.get('trained_addresses') or {})}
    json.dump(merged, open('${INSTANCE_DATA}', 'w'), indent=2)
    print(f"✅ Smart-merge: {len(merged['pump_patterns'])} pumps, {len(merged['dump_patterns'])} dumps")
except Exception as e:
    print(f"❌ Smart-merge failed: {e}")
PYEOF
            rm -f "$REMOTE_TMP"
        fi
        git add "$INSTANCE_DATA"
        git diff --cached --quiet || git commit -m "[merge] bot_data smart-merge on start" 2>/dev/null
        echo "✅ Smart-merge completed"
    else
        echo "✅ Updated to latest version"
    fi
fi

ENV_FILE=".env"
if [ "$1" = "v2" ] || [ -n "$BOT_INSTANCE" ]; then
    BOT_INSTANCE="${1:-$BOT_INSTANCE}"
    if [ -f ".env.${BOT_INSTANCE}" ]; then
        ENV_FILE=".env.${BOT_INSTANCE}"
    elif [ "$1" = "v2" ] && [ -f ".env.v2" ]; then
        ENV_FILE=".env.v2"
    fi
fi

if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "❌ ERROR: $ENV_FILE not found!"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Your secrets are missing. To fix:"
    echo "  1. Check if backup exists: ls /tmp/env_backup_* /tmp/.env*"
    echo "  2. Recreate from example: cp .env.example .env"
    echo "  3. Edit .env and add BOT_TOKEN, CHAT_ID, HELIUS_API_KEY"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Refusing to start without env file."
    exit 1
fi

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

BOT_INSTANCE="${BOT_INSTANCE:-main}"

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

echo "🤖 Opencode Bot Starting... [instance: $BOT_INSTANCE]"
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

# Kill ONLY this instance's old processes (not other instances)
echo "🧹 Cleaning up old processes for instance: $BOT_INSTANCE..."
if [ "$BOT_INSTANCE" = "main" ]; then
    # Main instance: kill processes matching meme_bot.py with default .env
    pkill -9 -f "meme_bot.py" 2>/dev/null || true
else
    # Non-main instance: kill only processes that loaded this instance's env
    pkill -9 -f "meme_bot.py.*$BOT_INSTANCE" 2>/dev/null || true
    # Also kill by lock file
    LOCK_CHECK="/tmp/meme_bot_${BOT_INSTANCE}.lock"
    if [ -f "$LOCK_CHECK" ]; then
        OLD_PID=$(cat "$LOCK_CHECK" 2>/dev/null)
        if [ -n "$OLD_PID" ] && [ -d "/proc/$OLD_PID" ]; then
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
        rm -f "$LOCK_CHECK"
    fi
fi
pkill -9 -f "daemon.sh" 2>/dev/null || true
pkill -9 -f "run_247.sh" 2>/dev/null || true
pkill -9 -f "watchdog.sh" 2>/dev/null || true
sleep 2

LOCK_FILE="/tmp/meme_bot_${BOT_INSTANCE:-main}.lock"
rm -f "$LOCK_FILE"

INTERNET_WAIT=15
ATTEMPT=0
while true; do
    if check_internet "https://api.telegram.org"; then
        if [ $ATTEMPT -gt 0 ]; then
            echo "🌐 Internet back at $(date) (after $ATTEMPT retries)" >> "$LOG_FILE"
        fi
        ATTEMPT=0
        echo "🚀 Launching at $(date)"
        python3 meme_bot.py 2>&1 &
        BOT_PID=$!
        echo "$BOT_PID" > "$LOCK_FILE"
        wait "$BOT_PID" 2>/dev/null
        EXIT_CODE=$?
        echo "❌ Exited code=$EXIT_CODE at $(date), restart in 10s"
        sleep 10
    else
        ATTEMPT=$((ATTEMPT + 1))
        if [ $((ATTEMPT % 4)) -eq 1 ]; then
            echo "⏳ No internet (try $ATTEMPT) — waiting ${INTERNET_WAIT}s"
        fi
        sleep $INTERNET_WAIT
    fi
done
