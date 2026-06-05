#!/bin/bash
# Termux Quick Setup - One command to setup and run bot
# Usage: bash setup_termux.sh

cd "$(dirname "$0")"

echo "🚀 Opencode Bot - Termux Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env from template..."
    cp .env.example .env
    echo ""
    echo "⚠️  .env তৈরি হয়েছে! এখন এডিট করুন:"
    echo "    nano .env"
    echo ""
    echo "নিচের ৩টি ভ্যালু বসান:"
    echo "    BOT_TOKEN=আপনার_টোকেন"
    echo "    CHAT_ID=আপনার_চ্যাট_আইডি"
    echo "    HELIUS_API_KEY=আপনার_কী"
    echo ""
    echo "এডিট শেষে আবার চালু করুন: bash setup_termux.sh"
    exit 0
fi

# Check if dependencies installed
echo "📦 Checking dependencies..."
pip install -r requirements.txt -q 2>/dev/null

# Git pull latest
if [ -d .git ]; then
    echo "📥 Pulling latest from GitHub..."
    git pull origin main
fi

echo ""
echo "✅ সব রেডি!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 বট চালু করতে দিন:"
echo "    bash start.sh"
echo ""
echo "🛑 বন্ধ করতে: Ctrl+C"
echo "🔄 আবার চালু করতে: bash start.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
