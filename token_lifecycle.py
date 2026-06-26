"""
Token Lifecycle Tracker - Track tokens through stages, learn stage transitions.

Tracks:
- Each signal event per token (signal_history)
- Stage transitions (launch → featured → KOTH → pump_completed)
- Time intervals between stages
- Data metrics at each stage (MCP, liq, holders, volume, price_change)
- Which stage combinations predict pumps

Learns from historical data:
- Which stage transitions lead to pumps
- What data at each stage predicts success
- Time interval patterns (fast vs slow pumps)
"""

import json
import os
import logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger("meme_bot.token_lifecycle")

LIFECYCLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_lifecycle.json")

# GMGN signal types mapped to lifecycle stages
SIGNAL_TO_STAGE = {
    "FEATURED_NEW": "featured",
    "DEV_BOUGHT": "dev_active",
    "DEV_SOLD": "dev_exiting",
    "CTO": "community_takeover",
    "FDV_SURGE": "fdv_surge",
    "KOTH": "king_of_hill",
    "KOL_FOMO": "kol_fomo",
    "DEXSOCIAL": "dexsocial",
    "PUMP_COMPLETED": "pump_completed",
}

# Stage order for transition analysis
STAGE_ORDER = {
    "featured": 0,
    "dev_active": 1,
    "dev_exiting": 2,
    "community_takeover": 3,
    "fdv_surge": 4,
    "kol_fomo": 5,
    "dexsocial": 6,
    "king_of_hill": 7,
    "pump_completed": 8,
}


def _load_lifecycle():
    """Load lifecycle data from file."""
    try:
        if os.path.exists(LIFECYCLE_FILE):
            with open(LIFECYCLE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"tokens": {}, "transitions": {}, "stage_stats": {}}


def _save_lifecycle(data):
    """Save lifecycle data to file."""
    try:
        with open(LIFECYCLE_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save lifecycle: {e}")


def record_signal(ca: str, token_data: dict):
    """
    Record a signal event for a token.
    Called every time a signal is detected (first or repeat).
    Returns: {"is_new": bool, "stage": str, "prev_stage": str, "time_since_last": float}
    """
    lifecycle = _load_lifecycle()
    tokens = lifecycle.setdefault("tokens", {})

    now = datetime.now(timezone.utc).timestamp()
    signal_type = token_data.get("signal_type", "UNKNOWN")
    stage = SIGNAL_TO_STAGE.get(signal_type, signal_type.lower())

    if ca not in tokens:
        # First signal for this token
        tokens[ca] = {
            "symbol": token_data.get("symbol", "?"),
            "signal_history": [],
            "stages_seen": [],
            "first_seen": now,
            "last_seen": now,
            "launch_mcp": token_data.get("mcp", 0),
            "is_winner": False,
            "is_loser": False,
        }

    token_lc = tokens[ca]
    token_lc["last_seen"] = now

    # Create signal event record
    signal_event = {
        "stage": stage,
        "signal_type": signal_type,
        "timestamp": now,
        "mcp": token_data.get("mcp", 0),
        "liq_usd": token_data.get("liq_usd", 0),
        "liq_sol": token_data.get("liq_sol", 0),
        "holders": token_data.get("holders", 0),
        "price_change_5m": token_data.get("price_change_5m", 0),
        "price_change_1h": token_data.get("price_change_1h", 0),
        "price_change_6h": token_data.get("price_change_6h", 0),
        "txns_5m": token_data.get("txns_5m", 0),
        "volume_5m": token_data.get("volume_5m", 0),
        "dev_status": token_data.get("dev_status", "UNKNOWN"),
        "top10_pct": token_data.get("top10_pct", 0),
        "source_channel": token_data.get("source_channel", "?"),
        "source_channel_name": token_data.get("source_channel_name", "?"),
    }

    # Check if this stage was already seen
    prev_stage = None
    time_since_last = 0
    if token_lc["signal_history"]:
        last_event = token_lc["signal_history"][-1]
        prev_stage = last_event["stage"]
        time_since_last = now - last_event["timestamp"]

    # Add to history
    token_lc["signal_history"].append(signal_event)

    # Track unique stages
    if stage not in token_lc["stages_seen"]:
        token_lc["stages_seen"].append(stage)

    # Record transition
    if prev_stage and prev_stage != stage:
        transitions = lifecycle.setdefault("transitions", {})
        transition_key = f"{prev_stage}->{stage}"
        trans = transitions.setdefault(transition_key, {"count": 0, "pumped": 0, "avg_time": 0, "times": []})
        trans["count"] += 1
        trans["times"].append(time_since_last)
        # Keep only last 100 times
        if len(trans["times"]) > 100:
            trans["times"] = trans["times"][-100:]
        trans["avg_time"] = sum(trans["times"]) / len(trans["times"])

    # Update stage stats
    stage_stats = lifecycle.setdefault("stage_stats", {})
    stat = stage_stats.setdefault(stage, {
        "count": 0, "pumped": 0, "avg_mcp": 0, "avg_holders": 0,
        "avg_liq": 0, "avg_pc5m": 0, "avg_pc1h": 0,
    })
    stat["count"] += 1
    # Running average
    n = stat["count"]
    stat["avg_mcp"] = (stat["avg_mcp"] * (n-1) + signal_event["mcp"]) / n
    stat["avg_holders"] = (stat["avg_holders"] * (n-1) + signal_event["holders"]) / n
    stat["avg_liq"] = (stat["avg_liq"] * (n-1) + signal_event["liq_usd"]) / n
    stat["avg_pc5m"] = (stat["avg_pc5m"] * (n-1) + signal_event["price_change_5m"]) / n
    stat["avg_pc1h"] = (stat["avg_pc1h"] * (n-1) + signal_event["price_change_1h"]) / n

    is_new = len(token_lc["signal_history"]) == 1

    _save_lifecycle(lifecycle)

    if not is_new:
        logger.info(
            f"[LIFECYCLE] {token_lc['symbol']} stage={stage} prev={prev_stage} "
            f"time_gap={time_since_last:.0f}s total_signals={len(token_lc['signal_history'])} "
            f"stages={token_lc['stages_seen']}"
        )

    return {
        "is_new": is_new,
        "stage": stage,
        "prev_stage": prev_stage,
        "time_since_last": time_since_last,
        "total_signals": len(token_lc["signal_history"]),
        "stages_seen": token_lc["stages_seen"],
    }


def mark_outcome(ca: str, is_winner: bool, is_loser: bool, ath_multiplier: float = 0):
    """Mark token outcome for lifecycle learning."""
    lifecycle = _load_lifecycle()
    tokens = lifecycle.get("tokens", {})

    if ca in tokens:
        tokens[ca]["is_winner"] = is_winner
        tokens[ca]["is_loser"] = is_loser
        tokens[ca]["ath_multiplier"] = ath_multiplier

        # Update transition stats
        if is_winner:
            transitions = lifecycle.get("transitions", {})
            stages_seen = tokens[ca].get("stages_seen", [])
            for i in range(len(stages_seen) - 1):
                key = f"{stages_seen[i]}->{stages_seen[i+1]}"
                if key in transitions:
                    transitions[key]["pumped"] += 1

            # Update stage stats
            stage_stats = lifecycle.get("stage_stats", {})
            for stage in stages_seen:
                if stage in stage_stats:
                    stage_stats[stage]["pumped"] += 1

        _save_lifecycle(lifecycle)


def get_lifecycle_score(ca: str) -> dict:
    """
    Get lifecycle-based score for a token.
    Returns score based on:
    - How many stages it passed through
    - Time intervals between stages
    - Stage transition patterns that historically lead to pumps
    """
    lifecycle = _load_lifecycle()
    tokens = lifecycle.get("tokens", {})
    transitions = lifecycle.get("transitions", {})
    stage_stats = lifecycle.get("stage_stats", {})

    if ca not in tokens:
        return {"score": 0, "reason": "no lifecycle data", "stages": 0, "num_signals": 0, "transitions": []}

    token_lc = tokens[ca]
    stages_seen = token_lc.get("stages_seen", [])
    signal_history = token_lc.get("signal_history", [])
    num_signals = len(signal_history)

    score = 0
    reasons = []

    # 1. Stage diversity score (more unique stages = more attention = higher score)
    stage_count = len(stages_seen)
    if stage_count >= 4:
        score += 30
        reasons.append(f"{stage_count} unique stages (high attention)")
    elif stage_count >= 3:
        score += 20
        reasons.append(f"{stage_count} unique stages")
    elif stage_count >= 2:
        score += 10
        reasons.append(f"{stage_count} unique stages")
    else:
        score += 5
        reasons.append(f"{stage_count} stage only")

    # 2. Signal count score (more signals = more market attention)
    if num_signals >= 5:
        score += 20
        reasons.append(f"{num_signals} signals (heavy attention)")
    elif num_signals >= 3:
        score += 15
        reasons.append(f"{num_signals} signals")
    elif num_signals >= 2:
        score += 10
        reasons.append(f"{num_signals} signals")

    # 3. Transition pattern score
    # Check if this token's transitions match known pump patterns
    for i in range(len(stages_seen) - 1):
        key = f"{stages_seen[i]}->{stages_seen[i+1]}"
        if key in transitions:
            trans = transitions[key]
            pump_rate = trans["pumped"] / max(trans["count"], 1)
            if pump_rate > 0.1:  # >10% pump rate for this transition
                score += 15
                reasons.append(f"transition {key} has {pump_rate:.0%} pump rate")
            elif pump_rate > 0.05:
                score += 8
                reasons.append(f"transition {key} has {pump_rate:.0%} pump rate")

    # 4. Time interval analysis
    # Fast transitions (< 5 min) between stages can indicate strong momentum
    if len(signal_history) >= 2:
        intervals = []
        for i in range(1, len(signal_history)):
            interval = signal_history[i]["timestamp"] - signal_history[i-1]["timestamp"]
            intervals.append(interval)

        avg_interval = sum(intervals) / len(intervals) if intervals else 0
        min_interval = min(intervals) if intervals else 0

        if min_interval < 300:  # < 5 min between any two signals
            score += 15
            reasons.append(f"fast transition {min_interval:.0f}s")
        elif min_interval < 600:  # < 10 min
            score += 10
            reasons.append(f"quick transition {min_interval:.0f}s")

        # Check if intervals are decreasing (accelerating attention)
        if len(intervals) >= 3:
            if intervals[-1] < intervals[0] * 0.5:
                score += 10
                reasons.append("accelerating attention")

    # 5. Data at latest stage
    if signal_history:
        latest = signal_history[-1]
        if latest["holders"] > 50:
            score += 10
            reasons.append(f"strong holder base ({latest['holders']})")
        if latest["volume_5m"] > 1000:
            score += 10
            reasons.append(f"volume spike (${latest['volume_5m']:.0f})")
        if latest["price_change_5m"] > 10:
            score += 10
            reasons.append(f"price momentum ({latest['price_change_5m']:.0f}%)")

    # Cap score at 100
    score = min(score, 100)

    return {
        "score": score,
        "reason": " | ".join(reasons) if reasons else "no notable patterns",
        "stages": stage_count,
        "num_signals": num_signals,
        "stages_seen": stages_seen,
        "transitions": [f"{stages_seen[i]}->{stages_seen[i+1]}" for i in range(len(stages_seen)-1)],
    }


def get_all_lifecycle_data():
    """Return full lifecycle data for analysis."""
    return _load_lifecycle()


def get_transition_stats():
    """Get all transition statistics."""
    lifecycle = _load_lifecycle()
    transitions = lifecycle.get("transitions", {})
    result = []
    for key, val in sorted(transitions.items(), key=lambda x: x[1].get("pumped", 0), reverse=True):
        result.append({
            "transition": key,
            "count": val["count"],
            "pumped": val["pumped"],
            "pump_rate": val["pumped"] / max(val["count"], 1),
            "avg_time_seconds": val.get("avg_time", 0),
        })
    return result


def get_stage_stats():
    """Get all stage statistics."""
    lifecycle = _load_lifecycle()
    return lifecycle.get("stage_stats", {})


def backfill_from_tracked_tokens(tracked_tokens: dict):
    """Backfill lifecycle data from existing tracked tokens."""
    lifecycle = _load_lifecycle()
    tokens = lifecycle.setdefault("tokens", {})
    added = 0

    for ca, data in tracked_tokens.items():
        if not isinstance(data, dict) or "symbol" not in data:
            continue
        if ca in tokens:
            continue

        signal_type = data.get("signal_type", "UNKNOWN")
        stage = SIGNAL_TO_STAGE.get(signal_type, signal_type.lower())

        tokens[ca] = {
            "symbol": data.get("symbol", "?"),
            "signal_history": [{
                "stage": stage,
                "signal_type": signal_type,
                "timestamp": data.get("first_seen", 0),
                "mcp": data.get("mcp", 0),
                "liq_usd": data.get("liq_usd", 0),
                "liq_sol": data.get("liq_sol", 0),
                "holders": data.get("holders", 0),
                "price_change_5m": data.get("price_change_5m", 0),
                "price_change_1h": data.get("price_change_1h", 0),
                "price_change_6h": data.get("price_change_6h", 0),
                "txns_5m": data.get("txns_5m", 0),
                "volume_5m": data.get("volume_5m", 0),
                "dev_status": data.get("dev_status", "UNKNOWN"),
                "top10_pct": data.get("top10_pct", 0),
                "source_channel": data.get("source_channel", "?"),
                "source_channel_name": data.get("source_channel_name", "?"),
            }],
            "stages_seen": [stage],
            "first_seen": data.get("first_seen", 0),
            "last_seen": data.get("last_check", data.get("first_seen", 0)),
            "launch_mcp": data.get("launch_mcp", 0),
            "is_winner": data.get("is_winner", False),
            "is_loser": data.get("is_loser", False),
            "ath_multiplier": data.get("ath_multiplier", 1),
        }

        # Mark winners/losers
        if data.get("is_winner"):
            tokens[ca]["is_winner"] = True
            stage_stats = lifecycle.setdefault("stage_stats", {})
            if stage not in stage_stats:
                stage_stats[stage] = {"count": 0, "pumped": 0, "avg_mcp": 0, "avg_holders": 0, "avg_liq": 0, "avg_pc5m": 0, "avg_pc1h": 0}
            stage_stats[stage]["pumped"] += 1

        added += 1

    _save_lifecycle(lifecycle)
    logger.info(f"[LIFECYCLE] Backfilled {added} tokens from tracked data")
    return added
