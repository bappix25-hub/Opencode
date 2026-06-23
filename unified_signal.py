"""
unified_signal.py — ONE signal: multi-source confirmed + DexScreener verified.

Flow:
1. Token detected from Telegram channel(s)
2. Compare against learned winner patterns
3. Check how many sources confirmed it
4. FINAL: DexScreener health check
5. Signal ONLY if all gates pass

"Early stage" = confirmed fast, not necessarily at launch.
"""

import json
import os
import math
from datetime import datetime, timezone

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")


def _load_all_data():
    tokens = {}
    try:
        with open(DATA_FILE) as f:
            tokens = json.load(f)
    except Exception:
        pass
    return tokens


def _get_profiles(tokens: dict):
    winners, losers = [], []
    for ca, t in tokens.items():
        status = t.get("status", "")
        ath = t.get("ath_multiplier", 0)
        profile = {
            "launch_mcp": t.get("launch_mcp", 0) or t.get("mcp", 0),
            "liq_usd": t.get("liq_usd", 0),
            "holders": t.get("holders", 0),
            "signal_type": t.get("signal_type", ""),
            "source_channel": t.get("source_channel", ""),
            "renounced": t.get("renounced", False),
        }
        if status in ("winner", "mega_winner") or ath >= 5:
            winners.append(profile)
        elif status == "loser":
            losers.append(profile)
    return winners, losers


def _compute_medians(profiles, key):
    vals = sorted([p[key] for p in profiles if p[key] > 0])
    if not vals:
        return 0
    n = len(vals)
    return (vals[n//2] + vals[(n-1)//2]) / 2 if n % 2 == 0 else vals[n//2]


def _health_gate(token: dict) -> dict:
    """Is this token alive or dead? Returns {"alive": bool, "reason": str}"""
    mcp = token.get("mcp", 0) or token.get("launch_mcp", 0)
    liq = token.get("liq_usd", 0)
    holders = token.get("holders", 0)
    ath_mcp = token.get("ath_mcp", 0)

    if mcp <= 0 or mcp < 100:
        return {"alive": False, "reason": "MC $0 — dead"}
    if liq <= 0 and mcp > 0:
        return {"alive": False, "reason": "Zero liquidity"}
    if ath_mcp > 0 and mcp > 0:
        drop = 1.0 - (mcp / ath_mcp)
        if drop > 0.85:
            return {"alive": False, "reason": f"Dumped {drop:.0%} from ATH"}
    if holders > 0 and holders <= 2:
        return {"alive": True, "reason": "Very few holders", "penalty": 0.3}

    return {"alive": True, "reason": "", "penalty": 0.0}


def _multi_source_score(token: dict, all_tokens: dict) -> float:
    """
    How many sources have confirmed this token?
    More independent sources = higher confidence.
    Score 0-1.
    """
    ca = token.get("ca", "")
    if not ca:
        return 0.3  # No address, can't check

    # Count unique channels that have seen this token
    sources = set()
    for t_addr, t_data in all_tokens.items():
        if t_addr == ca:
            ch = t_data.get("source_channel", "")
            if ch and ch != "?":
                sources.add(ch)
            # Also count GMGN as a source
            if t_data.get("signal_type", "") in ("KOTH", "FDV_SURGE", "CTO", "DEV_BOUGHT", "DEV_SOLD", "KOL_FOMO", "PUMP_COMPLETED"):
                sources.add("gmgn")

    # Also check current token's source
    current_ch = token.get("source_channel", "")
    if current_ch and current_ch != "?":
        sources.add(current_ch)
    if token.get("signal_type", "") in ("KOTH", "FDV_SURGE", "CTO", "DEV_BOUGHT", "DEV_SOLD", "KOL_FOMO", "PUMP_COMPLETED"):
        sources.add("gmgn")

    num_sources = len(sources)

    # 1 source = 0.3 (not confirmed)
    # 2 sources = 0.6 (confirmed by 2)
    # 3+ sources = 0.8 (strong confirmation)
    if num_sources >= 3:
        return 0.8
    elif num_sources >= 2:
        return 0.6
    elif num_sources == 1:
        return 0.3
    else:
        return 0.2


def _winner_fit_score(token: dict, winners: list) -> float:
    """How similar to known winners? 0-1."""
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

    if token_mcp > 0 and win_mcp_med > 0:
        wght = 4.0
        ratio = token_mcp / win_mcp_med
        sim = max(0, 1.0 - abs(math.log10(max(ratio, 0.01))) / 2.0)
        score += sim * wght
        total_w += wght

    if token_liq > 0 and win_liq_med > 0:
        wght = 3.0
        log_diff = abs(math.log10(max(token_liq, 1)) - math.log10(max(win_liq_med, 1)))
        sim = max(0, 1.0 - log_diff / 3.0)
        score += sim * wght
        total_w += wght

    if token_hold > 0 and win_hold_med > 0:
        wght = 2.0
        dist = min(abs(token_hold - win_hold_med) / 30, 1.0)
        score += (1.0 - dist) * wght
        total_w += wght

    wght = 1.0
    renounced_wins = sum(1 for w in winners if w["renounced"])
    renounced_rate = renounced_wins / len(winners) if winners else 0.5
    if token.get("renounced", False):
        score += renounced_rate * wght
    else:
        score += (1.0 - renounced_rate) * wght
    total_w += wght

    return (score / total_w) if total_w > 0 else 0.5


def _fundamentals_score(token: dict) -> float:
    """Token quality."""
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

    if token.get("renounced", False):
        score += 0.1

    return max(0.0, min(1.0, score))


def score_token(token: dict, dex_health: dict = None) -> dict:
    """
    UNIFIED SIGNAL — Multi-source confirmed + health verified.

    Gates:
    1. Health gate: is the token alive?
    2. Winner fit: does it match winner patterns?
    3. Multi-source: how many sources confirmed?
    4. DexScreener: final health check (if available)

    Signal only if:
    - Token is alive (health gate)
    - Winner fit >= 0.4
    - DexScreener healthy (if checked)
    """
    # ===== GATE 1: HEALTH =====
    health = _health_gate(token)
    if not health["alive"]:
        return {
            "score": 5.0,
            "verdict": "DEAD",
            "action": "SKIP",
            "breakdown": {},
            "reason": health["reason"],
            "dex_verified": False,
        }

    tokens_data = _load_all_data()
    winners, losers = _get_profiles(tokens_data)

    # ===== SCORING =====
    winner_fit = _winner_fit_score(token, winners)
    multi_src = _multi_source_score(token, tokens_data)
    fundamentals = _fundamentals_score(token)

    # Early detection (how low is MCP?)
    launch_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0)
    if launch_mcp > 0:
        early = 1.0 / (1.0 + math.log10(max(launch_mcp / 200, 0.1)))
        early = max(0.05, min(1.0, early))
    else:
        early = 0.3

    # Raw score
    raw_score = (
        early * 0.25 +
        winner_fit * 0.30 +
        multi_src * 0.25 +
        fundamentals * 0.20
    )

    # Health penalty
    raw_score *= (1.0 - health.get("penalty", 0) * 0.5)

    # ===== GATE 2: DEX SCREENER (if available) =====
    dex_verified = False
    dex_reason = ""
    if dex_health:
        if dex_health.get("healthy"):
            dex_verified = True
            dex_reason = dex_health.get("reason", "")
            # Bonus for DexScreener verified
            raw_score = min(1.0, raw_score * 1.15)
        else:
            # DexScreener says unhealthy — heavy penalty
            dex_reason = dex_health.get("reason", "")
            raw_score *= 0.3  # 70% penalty

    final_score = round(raw_score * 100, 1)

    # ===== VERDICT =====
    # Require: alive + winner_fit >= 0.4 + (dex verified OR 2+ sources)
    can_signal = (
        health["alive"] and
        winner_fit >= 0.4 and
        (dex_verified or multi_src >= 0.6)
    )

    if not can_signal:
        if final_score >= 50:
            verdict = "WATCH"
            action = "MONITOR"
        else:
            verdict = "WEAK"
            action = "SKIP"
    elif final_score >= 75:
        verdict = "STRONG"
        action = "BUY_NOW"
    elif final_score >= 60:
        verdict = "GOOD"
        action = "ALERT"
    elif final_score >= 45:
        verdict = "WATCH"
        action = "MONITOR"
    else:
        verdict = "WEAK"
        action = "SKIP"

    # Reasons
    reasons = []
    if launch_mcp > 0 and launch_mcp < 1000:
        reasons.append(f"Very early (MCP ${launch_mcp:,.0f})")
    elif launch_mcp > 0 and launch_mcp < 5000:
        reasons.append(f"Early (MCP ${launch_mcp:,.0f})")

    if health["reason"]:
        reasons.append(f"⚠️ {health['reason']}")

    if dex_verified:
        reasons.append(f"DexScreener ✅ {dex_reason}")
    elif dex_health and not dex_health.get("healthy"):
        reasons.append(f"DexScreener ❌ {dex_reason}")

    if multi_src >= 0.6:
        reasons.append("Multi-source confirmed")
    elif multi_src < 0.3:
        reasons.append("Single source only")

    if winner_fit >= 0.7:
        reasons.append("Matches winner profile")
    elif winner_fit < 0.4:
        reasons.append("Doesn't match winners")

    if fundamentals >= 0.7:
        reasons.append("Strong fundamentals")

    reason = " | ".join(reasons) if reasons else "Insufficient data"

    return {
        "score": final_score,
        "verdict": verdict,
        "action": action,
        "breakdown": {
            "early_detection": round(early * 100, 1),
            "winner_fit": round(winner_fit * 100, 1),
            "multi_source": round(multi_src * 100, 1),
            "fundamentals": round(fundamentals * 100, 1),
        },
        "reason": reason,
        "dex_verified": dex_verified,
        "num_winners": len(winners),
        "num_losers": len(losers),
    }


def score_token_quick(token: dict) -> float:
    return score_token(token)["score"]
