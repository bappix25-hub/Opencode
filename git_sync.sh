#!/bin/bash
# Auto-push to GitHub after code changes
# Usage: bash git_sync.sh [message]

cd "$(dirname "$0")" || exit 1

MSG="${1:-Auto-sync: $(date -u +%Y-%m-%dT%H:%M:%SZ)}"

# Stash bot_data.json before pull to avoid conflicts
if [ -f bot_data.json ]; then
    cp bot_data.json /tmp/botdata_pre_sync_$$.json
fi

git add -A
git diff --cached --quiet && exit 0

# Pull with merge (NOT rebase) for bot_data.json
git pull --no-rebase origin main 2>/dev/null

git commit -m "$MSG"
git push origin main 2>&1

PUSH_EXIT=$?

# Restore local bot_data.json if we backed it up
if [ -f /tmp/botdata_pre_sync_$$.json ]; then
    cp /tmp/botdata_pre_sync_$$.json bot_data.json
    rm -f /tmp/botdata_pre_sync_$$.json
    # Smart-merge local + remote
    git fetch origin main 2>/dev/null
    git checkout origin/main -- bot_data.json 2>/dev/null
    REMOTE_DATA="/tmp/remote_data_$$.json"
    cp bot_data.json "$REMOTE_DATA"
    cp /tmp/botdata_local_$$.json bot_data.json 2>/dev/null
    python3 -c "
import json, sys
try:
    local = json.load(open('/tmp/botdata_local_$$.json'))
    remote = json.load(open('$REMOTE_DATA'))
    def merge_list(a, b, key='address', cap=100):
        seen, out = set(), []
        for it in (a or []) + (b or []):
            k = it.get(key)
            if k and k in seen: continue
            if k: seen.add(k)
            out.append(it)
        return out[:cap]
    merged = dict(remote)
    merged['pump_patterns'] = merge_list(local.get('pump_patterns'), remote.get('pump_patterns'))
    merged['dump_patterns'] = merge_list(local.get('dump_patterns'), remote.get('dump_patterns'))
    merged['launch_patterns'] = merge_list(local.get('launch_patterns'), remote.get('launch_patterns'))
    merged['trained_addresses'] = {**(local.get('trained_addresses') or {}), **(remote.get('trained_addresses') or {})}
    json.dump(merged, open('bot_data.json','w'), indent=2)
    print(f'Merged: {len(merged[\"pump_patterns\"])} pumps, {len(merged[\"dump_patterns\"])} dumps')
except Exception as e:
    print(f'Merge skipped: {e}', file=sys.stderr)
" 2>/dev/null
    rm -f "$REMOTE_DATA"
    # Push merged result
    git add bot_data.json
    git diff --cached --quiet || git commit -m "[merge] bot_data smart-merge" 2>/dev/null
    git push origin main 2>&1
fi

if [ $PUSH_EXIT -eq 0 ]; then
    echo "✅ GitHub push success: $MSG"
else
    echo "❌ GitHub push failed"
fi
