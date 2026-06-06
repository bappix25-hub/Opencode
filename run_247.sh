#!/bin/bash
# 24/7 runner for Opencode Bot — survives disconnects & internet loss
# Run with: bash run_247.sh

cd "$(dirname "$0")"

SESSION="opencode-bot"
LOG_FILE="./bot.log"

ENV_FILE=".env"
if [ -f ".env.v2" ] && [ "$1" = "v2" ]; then
    ENV_FILE=".env.v2"
fi

kill_session() {
    tmux kill-session -t "$SESSION" 2>/dev/null
}

start_bot() {
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi

    echo "🤖 Starting Opencode Bot 24/7..."
    echo "📅 $(date)"
    echo "📂 Env: $ENV_FILE"
    echo "📝 Log: $LOG_FILE"
    echo "━━━━━━━━━━━━━━━━━━━━"

    kill_session 2>/dev/null
    tmux new-session -d -s "$SESSION" "while true; do python3 meme_bot.py 2>&1 | tee -a $LOG_FILE; echo \"Bot exited — restarting in 5s...\"; sleep 5; done"
    echo "✅ Bot started in tmux session: $SESSION"
    echo "📋 View logs: tmux attach -t $SESSION"
    echo "📋 Tail logs: tail -f $LOG_FILE"
}

internet_loop() {
    while true; do
        if curl -fsS -m 5 -o /dev/null "https://api.telegram.org" 2>/dev/null; then
            if ! tmux has-session -t "$SESSION" 2>/dev/null; then
                echo "🌐 Internet OK — restarting bot at $(date)" >> "$LOG_FILE"
                start_bot
            fi
        else
            if tmux has-session -t "$SESSION" 2>/dev/null; then
                echo "📡 Internet lost — pausing bot at $(date)" >> "$LOG_FILE"
                tmux send-keys -t "$SESSION" C-c 2>/dev/null
                sleep 3
                kill_session 2>/dev/null
            fi
        fi
        sleep 20
    done
}

start_bot
echo "👁️  Internet watchdog started (checks every 20s)"
internet_loop
