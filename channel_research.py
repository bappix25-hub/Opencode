"""
channel_research.py — Historical Channel Token Research

Research old tokens from Telegram channels:
1. Fetch old messages from channels
2. Parse tokens + capture features at signal time
3. Check current outcome (winner/loser/pending)
4. Learn channel+feature patterns

This replaces assumption-based scoring with data-driven patterns.
"""

import json
import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("channel_research")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
RESEARCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_research.json")
LEARNER_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")


def load_research() -> dict:
    try:
        with open(RESEARCH_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tokens": {}, "channel_patterns": {}, "last_research": None}


def save_research(data: dict):
    with open(RESEARCH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_tracked() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_learner() -> dict:
    try:
        with open(LEARNER_DATA) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ══════════════════════════════════════════════════════════════
# Step 1: Research all tracked tokens — build feature database
# ══════════════════════════════════════════════════════════════

def research_all_tokens() -> dict:
    """Research all tracked tokens: capture features, outcomes, channel patterns."""
    tracked = load_tracked()
    research = load_research()
    tokens = research.setdefault("tokens", {})

    now = datetime.now(timezone.utc).timestamp()
    stats = {"total": 0, "with_outcome": 0, "winners": 0, "losers": 0, "new_research": 0}

    for addr, t in tracked.items():
        stats["total"] += 1

        # Skip if already researched recently
        if addr in tokens and tokens[addr].get("researched_at"):
            if now - tokens[addr]["researched_at"] < 3600:
                continue

        # Build research entry
        status = t.get("status", "tracking")
        ath = t.get("ath_multiplier", 1)
        holders = t.get("holders", 0)
        liq = t.get("liq_usd", 0)
        mcp = t.get("mcp", 0)
        channel = t.get("source_channel_name", "unknown")
        signal_type = t.get("signal_type", "")
        first_seen = t.get("first_seen", 0)

        # Determine outcome
        if status == "mega_winner":
            outcome = "mega_win"
            stats["winners"] += 1
        elif status == "winner":
            outcome = "win"
            stats["winners"] += 1
        elif status == "loser":
            outcome = "loss"
            stats["losers"] += 1
        elif first_seen > 0:
            age_hours = (now - first_seen) / 3600
            if age_hours > 6:
                # Old enough to have outcome
                if ath < 0.5:
                    outcome = "loss"
                    stats["losers"] += 1
                elif ath >= 5:
                    outcome = "win"
                    stats["winners"] += 1
                else:
                    outcome = "flat"
            else:
                outcome = "pending"
        else:
            outcome = "unknown"

        if outcome != "pending":
            stats["with_outcome"] += 1

        # Capture feature snapshot
        tokens[addr] = {
            "symbol": t.get("symbol", "?"),
            "channel": channel,
            "signal_type": signal_type,
            "outcome": outcome,
            "ath_multiplier": ath,
            "features_at_signal": {
                "holders": holders,
                "liq_usd": liq,
                "mcp": mcp,
                "dev_balance_sol": t.get("dev_balance_sol", 0),
                "renounced": t.get("renounced", False),
                "initial_lp_pct": t.get("initial_lp_pct", 0),
                "top10_pct": t.get("top10_pct", 0),
            },
            "first_seen": first_seen,
            "researched_at": now,
        }
        stats["new_research"] += 1

    # Learn channel patterns from research
    research["channel_patterns"] = _learn_channel_patterns(tokens)
    research["last_research"] = datetime.now(timezone.utc).isoformat()

    save_research(research)
    return stats


def _learn_channel_patterns(tokens: dict) -> dict:
    """Learn which channel + feature combinations produce winners."""
    patterns = {}

    for addr, t in tokens.items():
        ch = t.get("channel", "unknown")
        outcome = t.get("outcome", "unknown")
        features = t.get("features_at_signal", {})
        signal_type = t.get("signal_type", "")

        if ch not in patterns:
            patterns[ch] = {
                "total": 0, "wins": 0, "losses": 0,
                "avg_holders_win": [], "avg_holders_loss": [],
                "avg_liq_win": [], "avg_liq_loss": [],
                "signal_types": {},
            }

        p = patterns[ch]
        p["total"] += 1

        if outcome in ("win", "mega_win"):
            p["wins"] += 1
            if features.get("holders", 0) > 0:
                p["avg_holders_win"].append(features["holders"])
            if features.get("liq_usd", 0) > 0:
                p["avg_liq_win"].append(features["liq_usd"])
        elif outcome == "loss":
            p["losses"] += 1
            if features.get("holders", 0) > 0:
                p["avg_holders_loss"].append(features["holders"])
            if features.get("liq_usd", 0) > 0:
                p["avg_liq_loss"].append(features["liq_usd"])

        if signal_type:
            st = p["signal_types"].setdefault(signal_type, {"total": 0, "wins": 0})
            st["total"] += 1
            if outcome in ("win", "mega_win"):
                st["wins"] += 1

    # Calculate averages and win rates
    for ch, p in patterns.items():
        p["win_rate"] = round(p["wins"] / p["total"] * 100, 1) if p["total"] else 0
        p["avg_holders_at_win"] = round(sum(p["avg_holders_win"]) / len(p["avg_holders_win"]), 1) if p["avg_holders_win"] else 0
        p["avg_holders_at_loss"] = round(sum(p["avg_holders_loss"]) / len(p["avg_holders_loss"]), 1) if p["avg_holders_loss"] else 0
        p["avg_liq_at_win"] = round(sum(p["avg_liq_win"]) / len(p["avg_liq_win"]), 0) if p["avg_liq_win"] else 0
        p["avg_liq_at_loss"] = round(sum(p["avg_liq_loss"]) / len(p["avg_liq_loss"]), 0) if p["avg_liq_loss"] else 0

        # Per signal type win rate
        for stype, st in p["signal_types"].items():
            st["win_rate"] = round(st["wins"] / st["total"] * 100, 1) if st["total"] else 0

        # Clean up temp arrays
        del p["avg_holders_win"]
        del p["avg_holders_loss"]
        del p["avg_liq_win"]
        del p["avg_liq_loss"]

    return patterns


# ══════════════════════════════════════════════════════════════
# Step 2: Get learned channel weights from data
# ══════════════════════════════════════════════════════════════

def get_learned_channel_weights() -> dict:
    """Get channel weights based on actual historical performance, not assumptions."""
    research = load_research()
    patterns = research.get("channel_patterns", {})
    weights = {}

    for ch, p in patterns.items():
        total = p.get("total", 0)
        win_rate = p.get("win_rate", 0)

        if total < 3:
            weights[ch] = 1.0  # Not enough data
            continue

        # Weight based on actual win rate
        # 50% win rate = 1.5x, 20% = 0.7x, 0% = 0.5x, 100% = 2.0x
        weight = 0.5 + (win_rate / 100) * 1.5
        weights[ch] = round(max(0.5, min(2.0, weight)), 2)

    return weights


# ══════════════════════════════════════════════════════════════
# Step 3: Predict using learned patterns
# ══════════════════════════════════════════════════════════════

def predict_from_channel_data(channel: str, signal_type: str, features: dict) -> dict:
    """Predict outcome based on learned channel+feature patterns.
    Returns {confidence, predicted_outcome, reason, win_rate_for_this_combo}."""
    research = load_research()
    patterns = research.get("channel_patterns", {})

    ch_pattern = patterns.get(channel)
    if not ch_pattern:
        return {"confidence": 0.5, "predicted_outcome": "unknown", "reason": f"No data for channel {channel}"}

    # Channel-level win rate
    ch_win_rate = ch_pattern.get("win_rate", 50) / 100

    # Signal type win rate
    st_win_rate = None
    st_data = ch_pattern.get("signal_types", {}).get(signal_type)
    if st_data and st_data.get("total", 0) >= 2:
        st_win_rate = st_data.get("win_rate", 50) / 100

    # Feature comparison: how does this token compare to winners?
    avg_holders_win = ch_pattern.get("avg_holders_at_win", 0)
    avg_liq_win = ch_pattern.get("avg_liq_at_win", 0)
    avg_holders_loss = ch_pattern.get("avg_holders_at_loss", 0)
    avg_liq_loss = ch_pattern.get("avg_liq_at_loss", 0)

    feature_score = 0.5  # neutral

    holders = features.get("holders", 0)
    liq = features.get("liq_usd", 0)

    if avg_holders_win > 0 and avg_liq_win > 0:
        # Token is more like winners if features are closer to winner averages
        h_ratio = min(holders, avg_holders_win) / max(holders, avg_holders_win) if max(holders, avg_holders_win) > 0 else 0
        l_ratio = min(liq, avg_liq_win) / max(liq, avg_liq_win) if max(liq, avg_liq_win) > 0 else 0
        feature_score = (h_ratio + l_ratio) / 2

    # Combine: channel win rate + signal type + features
    if st_win_rate is not None:
        combined = (ch_win_rate * 0.4) + (st_win_rate * 0.3) + (feature_score * 0.3)
    else:
        combined = (ch_win_rate * 0.5) + (feature_score * 0.5)

    # Determine prediction
    if combined >= 0.7:
        predicted = "pump"
        confidence = "HIGH"
    elif combined >= 0.5:
        predicted = "likely_pump"
        confidence = "MEDIUM"
    elif combined >= 0.3:
        predicted = "uncertain"
        confidence = "LOW"
    else:
        predicted = "likely_dump"
        confidence = "HIGH_NEGATIVE"

    # Build reason
    reasons = []
    reasons.append(f"ch={channel} wr={ch_win_rate*100:.0f}%")
    if st_win_rate is not None:
        reasons.append(f"sig={signal_type} wr={st_win_rate*100:.0f}%")
    if avg_holders_win > 0:
        reasons.append(f"holders={holders} vs_avg_win={avg_holders_win:.0f}")

    return {
        "confidence": round(combined, 3),
        "predicted_outcome": predicted,
        "confidence_level": confidence,
        "reason": " | ".join(reasons),
        "ch_win_rate": ch_win_rate,
        "st_win_rate": st_win_rate,
        "feature_score": feature_score,
    }


# ══════════════════════════════════════════════════════════════
# Step 4: Research report
# ══════════════════════════════════════════════════════════════

def get_research_report() -> str:
    """Human-readable research report."""
    research = load_research()
    patterns = research.get("channel_patterns", {})
    tokens = research.get("tokens", {})
    last = research.get("last_research", "Never")

    if not patterns:
        return "📊 No channel research yet. Run /research first."

    lines = ["📊 Channel Research Report", "=" * 40]
    lines.append(f"Last research: {last[:16] if last else 'Never'}")
    lines.append(f"Tokens researched: {len(tokens)}")

    # Sort by win rate
    ranked = sorted(patterns.items(), key=lambda x: -x[1].get("win_rate", 0))

    for ch, p in ranked:
        total = p.get("total", 0)
        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        wr = p.get("win_rate", 0)
        h_win = p.get("avg_holders_at_win", 0)
        l_win = p.get("avg_liq_at_win", 0)

        medal = "🥇" if wr >= 60 else "🥈" if wr >= 40 else "🥉" if wr >= 20 else "  "
        lines.append(f"\n{medal} {ch}")
        lines.append(f"   Total: {total} | Wins: {wins} | Losses: {losses} | WR: <b>{wr}%</b>")
        if h_win > 0:
            lines.append(f"   Avg holders at win: {h_win:.0f} | Avg liq at win: ${l_win:,.0f}")

        # Signal types
        stypes = p.get("signal_types", {})
        if stypes:
            for st, data in sorted(stypes.items(), key=lambda x: -x[1].get("win_rate", 0)):
                st_wr = data.get("win_rate", 0)
                st_total = data.get("total", 0)
                lines.append(f"   {st}: {st_total} signals, {st_wr}% WR")

    lines.append(f"\n{'=' * 40}")
    lines.append("💡 Use /predict <CA> to predict a token's outcome")
    return "\n".join(lines)
