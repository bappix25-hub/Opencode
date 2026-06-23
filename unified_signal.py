"""
unified_signal.py — ONE signal from multiple research inputs.

NOT "multiple channels said so" — that's convergence.
This learns from ALL data: winner patterns, loser patterns,
fundamentals, early-stage detection.

Key insight from data:
  - Winners: launch_mcp $276 median (caught VERY early)
  - Losers: launch_mcp $41,523 median (caught too late)
  - Liq/Holders: similar — liq alone doesn't predict

Signal = early_detection(35) + winner_fit(25) + loser_penalty(15)
         + fundamentals(15) + channel_intel(10)
"""

import json
import os
import math
from datetime import datetime, timezone
from typing import Optional

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
PATTERNS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")


def _load_all_data():
    tokens = {}
    try:
        with open(DATA_FILE) as f:
            tokens = json.load(f)
    except Exception:
        pass
    return tokens


def _get_profiles(tokens: dict):
    """Extract winner and loser feature profiles."""
    winners = []
    losers = []
    for ca, t in tokens.items():
        status = t.get("status", "")
        ath = t.get("ath_multiplier", 0)
        launch_mcp = t.get("launch_mcp", 0) or t.get("mcp", 0)
        profile = {
            "launch_mcp": launch_mcp,
            "liq_usd": t.get("liq_usd", 0),
            "holders": t.get("holders", 0),
            "signal_type": t.get("signal_type", ""),
            "source_channel": t.get("source_channel", ""),
            "renounced": t.get("renounced", False),
            "dev_balance_sol": t.get("dev_balance_sol", 0),
        }
        if status in ("winner", "mega_winner") or ath >= 5:
            winners.append(profile)
        elif status == "loser":
            losers.append(profile)
    return winners, losers


def _compute_medians(profiles, key):
    vals = [p[key] for p in profiles if p[key] > 0]
    if not vals:
        return 0
    vals.sort()
    n = len(vals)
    return (vals[n//2] + vals[(n-1)//2]) / 2 if n % 2 == 0 else vals[n//2]


def _early_detection_score(token: dict) -> float:
    """
    How early was this token detected?
    Winners median: $276, Losers median: $41,523.
    Earlier = better. Score 0-1.
    """
    launch_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0)
    if launch_mcp <= 0:
        return 0.3

    # Score: $100 -> 0.95, $500 -> 0.78, $1K -> 0.65, $5K -> 0.43, $20K -> 0.25, $50K+ -> 0.12
    score = 1.0 / (1.0 + math.log10(max(launch_mcp / 200, 0.1)))
    return max(0.05, min(1.0, score))


def _winner_fit_score(token: dict, winners: list) -> float:
    """
    How well does this token fit the winner profile?
    Higher = more similar to known winners.
    """
    if not winners:
        return 0.5

    win_liq_med = _compute_medians(winners, "liq_usd")
    win_hold_med = _compute_medians(winners, "holders")
    win_mcp_med = _compute_medians(winners, "launch_mcp")

    token_liq = token.get("liq_usd", 0)
    token_hold = token.get("holders", 0)
    token_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0)

    score = 0.0
    total_w = 0.0

    # MCP proximity to winner median (weight: 4) — MOST important
    if token_mcp > 0 and win_mcp_med > 0:
        wght = 4.0
        ratio = token_mcp / win_mcp_med
        # Perfect if same, drops off with distance
        sim = max(0, 1.0 - abs(math.log10(max(ratio, 0.01))) / 2.0)
        score += sim * wght
        total_w += wght

    # Liq proximity (weight: 3)
    if token_liq > 0 and win_liq_med > 0:
        wght = 3.0
        log_diff = abs(math.log10(max(token_liq, 1)) - math.log10(max(win_liq_med, 1)))
        sim = max(0, 1.0 - log_diff / 3.0)
        score += sim * wght
        total_w += wght

    # Holders proximity (weight: 2)
    if token_hold > 0 and win_hold_med > 0:
        wght = 2.0
        dist = min(abs(token_hold - win_hold_med) / 30, 1.0)
        score += (1.0 - dist) * wght
        total_w += wght

    # Renounced (weight: 1)
    wght = 1.0
    renounced_wins = sum(1 for w in winners if w["renounced"])
    renounced_rate = renounced_wins / len(winners) if winners else 0.5
    if token.get("renounced", False):
        score += renounced_rate * wght
    else:
        score += (1.0 - renounced_rate) * wght
    total_w += wght

    return (score / total_w) if total_w > 0 else 0.5


def _loser_penalty_score(token: dict, losers: list) -> float:
    """
    Penalty for matching loser profile.
    Returns 0-1 where 1 = no penalty (different from losers),
    0 = maximum penalty (matches losers perfectly).
    """
    if not losers:
        return 1.0  # No loser data, no penalty

    lose_liq_med = _compute_medians(losers, "liq_usd")
    lose_hold_med = _compute_medians(losers, "holders")
    lose_mcp_med = _compute_medians(losers, "launch_mcp")

    token_liq = token.get("liq_usd", 0)
    token_hold = token.get("holders", 0)
    token_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0)

    penalty = 0.0
    total_w = 0.0

    # MCP penalty — if close to loser median, penalize (weight: 4)
    if token_mcp > 0 and lose_mcp_med > 0:
        wght = 4.0
        ratio = token_mcp / lose_mcp_med
        # Close to loser median = high penalty
        closeness = max(0, 1.0 - abs(math.log10(max(ratio, 0.01))) / 2.0)
        penalty += closeness * wght
        total_w += wght

    # Liq penalty — if close to loser median (weight: 2)
    if token_liq > 0 and lose_liq_med > 0:
        wght = 2.0
        log_diff = abs(math.log10(max(token_liq, 1)) - math.log10(max(lose_liq_med, 1)))
        closeness = max(0, 1.0 - log_diff / 3.0)
        penalty += closeness * wght
        total_w += wght

    # Holders penalty (weight: 1)
    if token_hold > 0 and lose_hold_med > 0:
        wght = 1.0
        dist = min(abs(token_hold - lose_hold_med) / 30, 1.0)
        closeness = 1.0 - dist
        penalty += closeness * wght
        total_w += wght

    if total_w > 0:
        penalty_ratio = penalty / total_w  # 0-1, how much like a loser
        return 1.0 - penalty_ratio  # Invert: 1 = no penalty, 0 = max penalty

    return 1.0


def _fundamentals_score(token: dict) -> float:
    """Token fundamentals quality at early stage."""
    score = 0.5

    liq = token.get("liq_usd", 0)
    if 5000 <= liq <= 50000:
        score += 0.15
    elif liq > 50000:
        score += 0.1
    elif liq < 1000:
        score -= 0.1

    holders = token.get("holders", 0)
    if 5 <= holders <= 50:
        score += 0.1
    elif holders > 50:
        score += 0.05
    elif holders < 3:
        score -= 0.05

    dev_bal = token.get("dev_balance_sol", 0)
    if 0 < dev_bal < 5:
        score += 0.1
    elif dev_bal > 50:
        score -= 0.05

    if token.get("renounced", False):
        score += 0.1

    return max(0.0, min(1.0, score))


def _channel_intelligence_score(token: dict, tokens: dict) -> float:
    """Channel track record — learns over time."""
    ch = token.get("source_channel", "")
    if not ch:
        return 0.5

    ch_winners = 0
    ch_total = 0
    for ca, t in tokens.items():
        if t.get("source_channel") == ch:
            ch_total += 1
            if t.get("status") in ("winner", "mega_winner") or t.get("ath_multiplier", 0) >= 5:
                ch_winners += 1

    if ch_total < 5:
        return 0.5

    win_rate = ch_winners / ch_total
    return 0.2 + min(win_rate / 0.1, 0.6)


def score_token(token: dict) -> dict:
    """
    UNIFIED SIGNAL SCORE — One score from multiple research inputs.

    Components:
    - Early detection (35%): How early was this caught vs winner median?
    - Winner fit (25%): How similar to known winners?
    - Loser penalty (15%): How different from known losers?
    - Fundamentals (15%): Token quality metrics
    - Channel intelligence (10%): Channel track record
    """
    tokens_data = _load_all_data()
    winners, losers = _get_profiles(tokens_data)

    early = _early_detection_score(token)
    winner_fit = _winner_fit_score(token, winners)
    loser_pen = _loser_penalty_score(token, losers)
    fundamentals = _fundamentals_score(token)
    channel = _channel_intelligence_score(token, tokens_data)

    raw_score = (
        early * 0.35 +
        winner_fit * 0.25 +
        loser_pen * 0.15 +
        fundamentals * 0.15 +
        channel * 0.10
    )

    final_score = round(raw_score * 100, 1)

    if final_score >= 75:
        verdict = "STRONG"
        action = "BUY_NOW"
    elif final_score >= 60:
        verdict = "GOOD"
        action = "ALERT"
    elif final_score >= 45:
        verdict = "WATCH"
        action = "MONITOR"
    elif final_score >= 30:
        verdict = "WEAK"
        action = "SKIP"
    else:
        verdict = "POOR"
        action = "IGNORE"

    reasons = []
    launch_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0)
    if launch_mcp > 0 and launch_mcp < 1000:
        reasons.append(f"Very early (MCP ${launch_mcp:,.0f})")
    elif launch_mcp > 0 and launch_mcp < 5000:
        reasons.append(f"Early (MCP ${launch_mcp:,.0f})")
    elif launch_mcp > 20000:
        reasons.append(f"Late (MCP ${launch_mcp:,.0f})")

    if winner_fit >= 0.7:
        reasons.append("Matches winner profile")
    elif winner_fit < 0.3:
        reasons.append("Differs from winners")

    if loser_pen < 0.5:
        reasons.append("Matches loser profile!")
    elif loser_pen >= 0.8:
        reasons.append("Unlike losers")

    if fundamentals >= 0.7:
        reasons.append("Strong fundamentals")
    elif fundamentals < 0.3:
        reasons.append("Weak fundamentals")

    reason = " | ".join(reasons) if reasons else "Insufficient data"

    return {
        "score": final_score,
        "verdict": verdict,
        "action": action,
        "breakdown": {
            "early_detection": round(early * 100, 1),
            "winner_fit": round(winner_fit * 100, 1),
            "loser_penalty": round(loser_pen * 100, 1),
            "fundamentals": round(fundamentals * 100, 1),
            "channel_intelligence": round(channel * 100, 1),
        },
        "reason": reason,
        "num_winners": len(winners),
        "num_losers": len(losers),
    }


def score_token_quick(token: dict) -> float:
    return score_token(token)["score"]
