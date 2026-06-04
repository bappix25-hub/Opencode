#!/bin/bash
# Opencode Bot — One-liner Termux Bootstrapper
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/bappix25-hub/Opencode/main/bootstrap.sh)
#   bash bootstrap.sh                  # same effect
#
# What it does:
#   1. Detect: ~/Opencode exists? → pull : clone
#   2. Install: pip deps (idempotent)
#   3. Setup:   .env interactive (skips if real token present)
#   4. Launch:  start.sh (auto-restart loop)
#
# No secrets embedded. GitHub PAT-free clone (public repo).

set -e

REPO_URL="https://github.com/bappix25-hub/Opencode.git"
DIR="${OPENCODE_DIR:-$HOME/Opencode}"

echo "🤖 Opencode Bot Bootstrapper"
echo "━━━━━━━━━━━━━━━━━━━━"
echo "📅 $(date)"
echo "📂 Target: $DIR"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 not found. Termux: pkg install python"
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "❌ git not found. Termux: pkg install git"
    exit 1
fi

if [ -d "$DIR/.git" ]; then
    echo "📥 Repo exists — pulling updates..."
    cd "$DIR"
    if ! git pull --ff-only origin main 2>/dev/null; then
        echo "⚠️  Pull conflict — fetching + reset to origin/main"
        git fetch origin main && git reset --hard origin/main
    fi
else
    echo "📥 Cloning fresh repo..."
    mkdir -p "$(dirname "$DIR")"
    git clone "$REPO_URL" "$DIR"
    cd "$DIR"
fi
echo "✅ Repo ready: $(git rev-parse --short HEAD)"

echo ""
echo "📦 Installing Python deps..."
PIP_FLAGS="--upgrade --quiet --disable-pip-version-check"
if [ "$1" = "v2" ] || [ -n "$BOT_INSTANCE" ]; then
    PIP_FLAGS="$PIP_FLAGS --break-system-packages"
fi
if [ -f requirements.txt ]; then
    pip install $PIP_FLAGS -r requirements.txt 2>&1 | tail -3 || {
        echo "⚠️  pip install warning — trying with --force-reinstall"
        pip install --upgrade --force-reinstall $PIP_FLAGS -r requirements.txt 2>&1 | tail -5 || {
            echo "⚠️  pip install failed — continuing (deps may already be compatible)"
        }
    }
else
    echo "⚠️  requirements.txt missing — skipping"
fi
echo "✅ Deps ready"

echo ""
NEED_SETUP=0
if [ ! -f .env ]; then
    NEED_SETUP=1
elif grep -qE "^BOT_TOKEN=(YOUR[A-Z_]*|_HERE|)\s*$" .env 2>/dev/null; then
    NEED_SETUP=1
elif grep -qE "^[A-Z_]+=\s*$" .env 2>/dev/null; then
    NEED_SETUP=1
fi

if [ "$NEED_SETUP" -eq 1 ]; then
    if [ -t 0 ] && [ -t 1 ]; then
        echo "🔑 Token setup needed (interactive)..."
        bash setup_env.sh
    else
        echo "❌ .env missing/empty AND no interactive terminal."
        echo "   Run: bash setup_env.sh  (in TUI/SSH session)"
        exit 1
    fi
else
    echo "✅ .env found with tokens — skipping setup"
fi

echo ""
echo "🚀 Launching bot..."
exec bash start.sh
