"""
learned_scorer.py — Data-driven token scoring from actual winner patterns.

NO hardcoded penalties. Score = similarity to known winning tokens.
Learns from: telegram_tracked_tokens.json + bot_data.json pump patterns.
"""

import json
import os
import math
from datetime import datetime, timezone

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
PATTERNS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")


def _load_winners():
    """Load actual winning tokens and extract their features."""
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    winners = []
    for ca, t in data.items():
        if t.get("status") in ("winner", "mega_winner") and t.get("ath_multiplier", 0) >= 5:
            winners.append({
                "liq_usd": t.get("liq_usd", 0),
                "holders": t.get("holders", 0),
                "top10_pct": t.get("top10_pct", 0),
                "mcp": t.get("mcp", 0),
                "signal_type": t.get("signal_type", ""),
                "source_channel": t.get("source_channel_name", ""),
                "no_mint": t.get("no_mint", False),
                "blacklist_safe": t.get("blacklist_safe", False),
                "burnt": t.get("burnt", False),
                "volume_5m": t.get("volume_5m", 0),
                "txns_5m": t.get("txns_5m", 0),
                "ath_multiplier": t.get("ath_multiplier", 0),
            })
    return winners


def _load_pump_patterns():
    """Load learned pump patterns from bot_data.json."""
    try:
        with open(PATTERNS_FILE) as f:
            data = json.load(f)
        return data.get("pump_patterns", {})
    except Exception:
        return {}


def _feature_distance(val1, val2, max_range):
    """Calculate normalized distance between two values (0=identical, 1=very different)."""
    if max_range == 0:
        return 0
    return min(abs(val1 - val2) / max_range, 1.0)


def _categorical_match(val1, val2):
    """1.0 if identical, 0.0 if different."""
    return 1.0 if val1 == val2 else 0.0


def _boolean_match(val1, val2):
    """1.0 if both same, 0.5 if different."""
    return 1.0 if val1 == val2 else 0.5


def score_token(token: dict) -> dict:
    """
    Score a token based on similarity to known winners.
    Returns score 0.0-1.0, matched features, and explanation.
    NO hardcoded penalties — purely data-driven.
    """
    winners = _load_winners()
    if not winners:
        return {"score": 0.5, "verdict": "NO_DATA", "matched": [], "reason": "No winner data to compare"}

    features = {
        "liq_usd": token.get("liq_usd", 0),
        "holders": token.get("holders", 0),
        "top10_pct": token.get("top10_pct", 0),
        "mcp": token.get("mcp", 0),
        "signal_type": token.get("signal_type", ""),
        "source_channel": token.get("source_channel_name", ""),
        "no_mint": token.get("no_mint", False),
        "blacklist_safe": token.get("blacklist_safe", False),
        "burnt": token.get("burnt", False),
        "volume_5m": token.get("volume_5m", 0),
        "txns_5m": token.get("txns_5m", 0),
    }

    # Calculate similarity to each winner
    similarities = []
    matched_details = []

    for w in winners:
        sim = 0.0
        weights_total = 0.0

        # Liq similarity (weight: 3) — important feature
        if features["liq_usd"] > 0 and w["liq_usd"] > 0:
            w_liq = 3.0
            # Use log scale for liq (big differences matter less)
            log_diff = abs(math.log10(max(features["liq_usd"], 1)) - math.log10(max(w["liq_usd"], 1)))
            liq_sim = max(0, 1.0 - log_diff / 3.0)  # 3 orders of magnitude = 0 similarity
            sim += liq_sim * w_liq
            weights_total += w_liq

        # Holders similarity (weight: 2)
        if features["holders"] > 0 and w["holders"] > 0:
            w_hold = 2.0
            hold_dist = _feature_distance(features["holders"], w["holders"], 50)
            sim += (1.0 - hold_dist) * w_hold
            weights_total += w_hold

        # Top10 similarity (weight: 2)
        if features["top10_pct"] > 0 and w["top10_pct"] > 0:
            w_top = 2.0
            top_dist = _feature_distance(features["top10_pct"], w["top10_pct"], 100)
            sim += (1.0 - top_dist) * w_top
            weights_total += w_top

        # Signal type match (weight: 1.5)
        w_sig = 1.5
        sig_sim = _categorical_match(features["signal_type"], w["signal_type"])
        sim += sig_sim * w_sig
        weights_total += w_sig

        # Channel match (weight: 1)
        w_ch = 1.0
        ch_sim = _categorical_match(features["source_channel"], w["source_channel"])
        sim += ch_sim * w_ch
        weights_total += w_ch

        # Security features (weight: 0.5 each)
        w_sec = 0.5
        sim += _boolean_match(features["no_mint"], w["no_mint"]) * w_sec
        sim += _boolean_match(features["blacklist_safe"], w["blacklist_safe"]) * w_sec
        sim += _boolean_match(features["burnt"], w["burnt"]) * w_sec
        weights_total += w_sec * 3

        # Volume similarity (weight: 1)
        if features["volume_5m"] > 0 and w["volume_5m"] > 0:
            w_vol = 1.0
            vol_diff = abs(math.log10(max(features["volume_5m"], 1)) - math.log10(max(w["volume_5m"], 1)))
            vol_sim = max(0, 1.0 - vol_diff / 3.0)
            sim += vol_sim * w_vol
            weights_total += w_vol

        if weights_total > 0:
            sim /= weights_total
            similarities.append(sim)

    if not similarities:
        return {"score": 0.5, "verdict": "NO_DATA", "matched": [], "reason": "No comparable winners"}

    # Score = best similarity to any winner
    best_sim = max(similarities)
    avg_sim = sum(similarities) / len(similarities)

    # Combined score: 70% best match + 30% average (rewards tokens similar to MANY winners)
    score = best_sim * 0.7 + avg_sim * 0.3

    # Verdict
    if score >= 0.7:
        verdict = "STRONG"
    elif score >= 0.55:
        verdict = "GOOD"
    elif score >= 0.4:
        verdict = "FAIR"
    else:
        verdict = "WEAK"

    # Find matched features (what makes this token similar to winners)
    matched = []
    if features["liq_usd"] > 0:
        winner_liqs = [w["liq_usd"] for w in winners if w["liq_usd"] > 0]
        if winner_liqs:
            median_liq = sorted(winner_liqs)[len(winner_liqs) // 2]
            ratio = features["liq_usd"] / median_liq if median_liq > 1 else 0
            if 0.5 <= ratio <= 2.0:
                matched.append(f"Liq ${features['liq_usd']:,.0f} (winner median ${median_liq:,.0f})")

    if features["holders"] > 0:
        winner_holds = [w["holders"] for w in winners if w["holders"] > 0]
        if winner_holds:
            median_hold = sorted(winner_holds)[len(winner_holds) // 2]
            if abs(features["holders"] - median_hold) <= 5:
                matched.append(f"Holders {features['holders']} (winner median {median_hold})")

    if features["signal_type"]:
        sig_counts = {}
        for w in winners:
            s = w["signal_type"]
            sig_counts[s] = sig_counts.get(s, 0) + 1
        if features["signal_type"] in sig_counts:
            matched.append(f"Signal {features['signal_type']} seen in {sig_counts[features['signal_type']]} winners")

    return {
        "score": round(score, 3),
        "verdict": verdict,
        "best_similarity": round(best_sim, 3),
        "avg_similarity": round(avg_sim, 3),
        "matched": matched,
        "num_winners_compared": len(winners),
    }


def score_token_quick(token: dict) -> float:
    """Quick score (just the number) for batch processing."""
    result = score_token(token)
    return result["score"]
