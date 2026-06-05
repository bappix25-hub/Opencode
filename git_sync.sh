#!/bin/bash
# Auto-push to GitHub after code changes
# Usage: bash git_sync.sh [message]

cd "$(dirname "$0")" || exit 1

MSG="${1:-Auto-sync: $(date -u +%Y-%m-%dT%H:%M:%SZ)}"

git add -A
git diff --cached --quiet && exit 0

git commit -m "$MSG"
git push origin main 2>&1

if [ $? -eq 0 ]; then
    echo "✅ GitHub push success: $MSG"
else
    echo "❌ GitHub push failed"
fi
