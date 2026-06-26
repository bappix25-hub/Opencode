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
    mcp = token.get("mcp", 0) or token.get("launch_mcp", 0) or token.get("mcap", 0)
    liq = token.get("liq_usd", 0) or token.get("liquidity", 0)
    holders = token.get("holders", 0)
    ath_mcp = token.get("ath_mcp", 0)

    # Apply learned criteria if available
    learned = _load_learned_scorer()
    if learned and learned.get("signal_criteria"):
        criteria = learned["signal_criteria"]
        min_liq = criteria.get("min_liq", 0)
        min_holders = criteria.get("min_holders", 0)
        min_mcp = criteria.get("min_mcp", 0)
        min_wallets = criteria.get("min_wallets", 0)
        min_lp_locked = criteria.get("min_lp_locked", 0)
        max_top10_pct = criteria.get("max_top10_pct", 100)
        min_bsr = criteria.get("min_bsr", 0)
        
        if min_liq > 0 and liq > 0 and liq < min_liq:
            return {"alive": False, "reason": f"LP ${liq:,.0f} below minimum ${min_liq:,.0f}"}
        if min_holders > 0 and holders > 0 and holders < min_holders:
            return {"alive": False, "reason": f"Holders {holders} below minimum {min_holders}"}
        if min_mcp > 0 and mcp > 0 and mcp < min_mcp:
            return {"alive": False, "reason": f"MCP ${mcp:,.0f} below minimum ${min_mcp:,.0f}"}
        
        # New strict filters
        top10 = token.get("top10_pct", 0)
        lp_locked = token.get("lp_locked", 0)
        bsr = token.get("buy_sell_ratio", 0)
        
        if min_wallets > 0:
            wallets = token.get("unique_wallets", 0)
            if wallets > 0 and wallets < min_wallets:
                return {"alive": False, "reason": f"Wallets {wallets} below minimum {min_wallets}"}
        if min_lp_locked > 0 and lp_locked > 0 and lp_locked < min_lp_locked:
            return {"alive": False, "reason": f"LP locked {lp_locked:.0f}% below minimum {min_lp_locked}%"}
        if max_top10_pct > 0 and top10 > 0 and top10 > max_top10_pct:
            return {"alive": False, "reason": f"Top10 {top10:.0f}% above maximum {max_top10_pct}%"}
        if min_bsr > 0 and bsr > 0 and bsr < min_bsr:
            return {"alive": False, "reason": f"BSR {bsr:.2f} below minimum {min_bsr}"}

    # Dead token: MC too low
    if mcp <= 0 or mcp < 100:
        return {"alive": False, "reason": "MC $0 — dead"}

    # ZERO LIQUIDITY = suspicious but not instant block if holders are strong
    if liq <= 100 and mcp > 0:
        if holders > 20:
            return {"alive": True, "reason": "Zero LP but has holders", "penalty": 0.3}
        return {"alive": False, "reason": f"Zero LP (${liq:.0f}) — scam"}

    # NEAR-ZERO LIQUIDITY = BLOCK (can't trade)
    if liq > 0 and liq < 500:
        return {"alive": False, "reason": f"LP too low (${liq:.0f}) — can't exit"}

    # ATH drop = rug pull detection
    if ath_mcp > 0 and mcp > 0:
        drop = 1.0 - (mcp / ath_mcp)
        if drop > 0.70:
            return {"alive": False, "reason": f"Rug: dumped {drop:.0%} from ATH (${ath_mcp:,.0f}→${mcp:,.0f})"}
        if drop > 0.50:
            return {"alive": True, "reason": f"Warning: dropped {drop:.0%} from ATH", "penalty": 0.5}

    # Few holders = likely dead/scam
    if holders > 0 and holders <= 3:
        return {"alive": False, "reason": f"Only {holders} holders — dead"}
    if holders > 0 and holders <= 8:
        return {"alive": True, "reason": f"Very few holders ({holders})", "penalty": 0.3}

    # FAKE PUMP detection: ATH>500x with few holders = pump and dump
    ath_mult = token.get("ath_multiplier", 1)
    if ath_mult > 500 and holders < 10:
        return {"alive": False, "reason": f"Fake pump: ATH {ath_mult:.0f}x but only {holders} holders"}

    # Top10 insider control
    top10 = token.get("top10_pct", 0)
    if top10 > 80:
        return {"alive": False, "reason": f"Insider controlled: top10 holds {top10}%"}

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


def _load_learned_scorer():
    """Load learned scorer data from file."""
    try:
        scorer_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_scorer.json")
        if os.path.exists(scorer_file):
            with open(scorer_file) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _winner_fit_score(token: dict, winners: list) -> float:
    """How similar to known winners? 0-1. Uses learned_scorer.json if available."""
    learned = _load_learned_scorer()
    
    if learned and learned.get("winner_medians"):
        med = learned["winner_medians"]
        win_liq_med = med.get("liq_usd", 0)
        win_hold_med = med.get("holders", 0)
        win_mcp_med = med.get("launch_mcp", 0)
        # If medians are incomplete (only ath_multiplier), fall back to winner list
        if not win_liq_med and not win_hold_med and not win_mcp_med and winners:
            win_liq_med = _compute_medians(winners, "liq_usd")
            win_hold_med = _compute_medians(winners, "holders")
            win_mcp_med = _compute_medians(winners, "launch_mcp")
    elif winners:
        win_liq_med = _compute_medians(winners, "liq_usd")
        win_hold_med = _compute_medians(winners, "holders")
        win_mcp_med = _compute_medians(winners, "launch_mcp")
    else:
        return 0.5

    token_liq = token.get("liq_usd", 0) or token.get("liquidity", 0)
    token_hold = token.get("holders", 0)
    token_mcp = token.get("launch_mcp", 0) or token.get("mcp", 0) or token.get("mcap", 0)

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
    """Token quality. Returns 0-1."""
    score = 0.3  # base

    # Liquidity (0-0.25)
    liq = token.get("liq_usd", 0) or token.get("liquidity", 0)
    if liq > 20000:
        score += 0.25
    elif liq > 10000:
        score += 0.20
    elif liq > 5000:
        score += 0.15
    elif liq > 2000:
        score += 0.10
    elif liq > 500:
        score += 0.05

    # Holders (0-0.20)
    holders = token.get("holders", 0)
    if holders > 100:
        score += 0.20
    elif holders > 50:
        score += 0.15
    elif holders > 20:
        score += 0.10
    elif holders > 10:
        score += 0.08
    elif holders > 5:
        score += 0.05

    # Buy/sell ratio (0-0.15)
    bsr = token.get("buy_sell_ratio", 0) or token.get("bsr", 0)
    if bsr > 3.0:
        score += 0.15
    elif bsr > 2.0:
        score += 0.10
    elif bsr > 1.5:
        score += 0.05

    # LP locked (0-0.10)
    lp_locked = token.get("lp_locked", 0)
    if lp_locked > 90:
        score += 0.10
    elif lp_locked > 70:
        score += 0.05

    # Renounced (0-0.05)
    if token.get("renounced", False):
        score += 0.05

    # Insider count penalty
    insiders = token.get("insider_count", 0)
    if insiders > 5:
        score -= 0.10
    elif insiders > 3:
        score -= 0.05

    return max(0.0, min(1.0, score))


def _social_score(token: dict) -> float:
    """
    Social activity score: does the token have website, twitter, telegram?
    Long-time active tokens have strong social presence.
    Returns 0-1 (0 = no social, 1 = all socials present).
    """
    score = 0.0
    reasons = []
    
    # Check from token data (may be pre-populated by caller)
    has_website = token.get("has_website", False)
    has_twitter = token.get("has_twitter", False)
    has_telegram = token.get("has_telegram", False)
    socials = token.get("socials_list", [])
    
    # Also check socials list (from DexScreener)
    if socials:
        for s in socials:
            url = s.get("url", "")
            stype = s.get("type", "")
            if "twitter" in url or "x.com" in url or stype == "twitter":
                has_twitter = True
            elif "t.me" in url or stype == "telegram":
                has_telegram = True
    
    # Check websites list
    websites = token.get("websites_list", [])
    if websites:
        has_website = True
    
    if has_website:
        score += 0.35
        reasons.append("website")
    if has_twitter:
        score += 0.35
        reasons.append("twitter")
    if has_telegram:
        score += 0.30
        reasons.append("telegram")
    
    return score, reasons


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

    # ===== GATE 1.5: RUG PROBABILITY =====
    rug_prob = 0.0
    rug_reasons = []
    try:
        from rug_detector import get_rug_probability
        rug_data = get_rug_probability(token)
        rug_prob = rug_data["rug_prob"]
        rug_reasons = rug_data["reasons"]
    except Exception:
        pass

    # If rug probability >70%, block signal
    if rug_prob > 0.70:
        return {
            "score": 10.0,
            "verdict": "RUG_RISK",
            "action": "SKIP",
            "breakdown": {"rug_prob": round(rug_prob * 100, 1)},
            "reason": f"High rug risk ({rug_prob:.0%}): {'; '.join(rug_reasons)}",
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

    # Lifecycle score (multi-stage tracking, stage transitions)
    lifecycle_score = 0.0
    lifecycle_data = None
    try:
        from token_lifecycle import get_lifecycle_score
        ca = token.get("ca", "")
        if ca:
            lifecycle_data = get_lifecycle_score(ca)
            lifecycle_score = lifecycle_data["score"] / 100.0  # Normalize to 0-1
    except Exception:
        pass

    # Social activity score (website, twitter, telegram)
    social_score, social_reasons = _social_score(token)
    
    # Social history score (growth over time)
    social_history = 0.0
    try:
        from social_tracker import get_social_score_from_history
        ca = token.get("ca", "")
        if ca:
            social_history = get_social_score_from_history(ca)
    except Exception:
        pass

    # Raw score — reweighted: fundamentals + winner_fit dominant
    raw_score = (
        early * 0.10 +
        winner_fit * 0.30 +
        multi_src * 0.15 +
        fundamentals * 0.30 +
        lifecycle_score * 0.05 +
        social_score * 0.05 +
        social_history * 0.05
    )

    # TokenScan bonus/penalty
    ts_data = dex_health.get("data", {}) if dex_health else {}
    if ts_data.get("parsed") or ts_data.get("holders"):
        ts_holders = ts_data.get("holders", 0)
        ts_top10 = ts_data.get("top10_pct", 0)
        ts_bundled = ts_data.get("bundled_pct", 0)
        ts_audit = ts_data.get("audit_score", 0)
        # Good holders = bonus
        if ts_holders > 50:
            raw_score = min(1.0, raw_score * 1.05)
        elif ts_holders > 20:
            raw_score = min(1.0, raw_score * 1.02)
        # Low top10 = bonus
        if 0 < ts_top10 < 20:
            raw_score = min(1.0, raw_score * 1.05)
        elif ts_top10 > 40:
            raw_score *= 0.9
        # Low bundled = bonus
        if 0 < ts_bundled < 10:
            raw_score = min(1.0, raw_score * 1.03)
        elif ts_bundled > 25:
            raw_score *= 0.85
        # Good audit = bonus
        if ts_audit >= 7:
            raw_score = min(1.0, raw_score * 1.05)
        elif ts_audit < 4 and ts_audit > 0:
            raw_score *= 0.8

    # Cross-channel bonus: tokens in 2+ channels get boost
    try:
        from cross_channel import get_tracker
        cc = get_tracker()
        ca = token.get("ca", "")
        if ca:
            cc_data = cc.get_cross_channel_score(ca)
            if cc_data["channels"] >= 2:
                # Boost: +15% per extra channel (max +35%)
                raw_score = min(1.0, raw_score * (1.0 + cc_data["boost"]))
    except Exception:
        pass

    # Health penalty
    raw_score *= (1.0 - health.get("penalty", 0) * 0.5)

    # Rug probability penalty
    if rug_prob > 0.3:
        raw_score *= (1.0 - rug_prob * 0.5)

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
    # Require: alive + winner_fit >= 0.25 + (dex verified OR 2+ sources)
    can_signal = (
        health["alive"] and
        winner_fit >= 0.25 and
        (dex_verified or multi_src >= 0.5)
    )

    if not can_signal:
        if final_score >= 40:
            verdict = "WATCH"
            action = "MONITOR"
        else:
            verdict = "WEAK"
            action = "SKIP"
    elif final_score >= 65:
        verdict = "STRONG"
        action = "BUY_NOW"
    elif final_score >= 50:
        verdict = "GOOD"
        action = "ALERT"
    elif final_score >= 35:
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

    if rug_prob > 0.3:
        reasons.append(f"⚠️ Rug risk: {rug_prob:.0%}")
    if rug_reasons and rug_prob > 0.3:
        reasons.append(f"Rug indicators: {', '.join(rug_reasons[:2])}")

    if lifecycle_data:
        if lifecycle_data["stages"] >= 3:
            reasons.append(f"Lifecycle: {lifecycle_data['stages']} stages, {lifecycle_data['num_signals']} signals")
        elif lifecycle_data["stages"] >= 2:
            reasons.append(f"Lifecycle: {lifecycle_data['stages']} stages")
        if lifecycle_data.get("transitions"):
            reasons.append(f"Transitions: {', '.join(lifecycle_data['transitions'][:3])}")

    reason = " | ".join(reasons) if reasons else "Insufficient data"

    breakdown = {
        "early_detection": round(early * 100, 1),
        "winner_fit": round(winner_fit * 100, 1),
        "multi_source": round(multi_src * 100, 1),
        "fundamentals": round(fundamentals * 100, 1),
        "lifecycle": round(lifecycle_score * 100, 1),
        "rug_prob": round(rug_prob * 100, 1),
    }
    # Add cross-channel data to breakdown
    try:
        from cross_channel import get_tracker
        cc = get_tracker()
        ca = token.get("ca", "")
        if ca:
            cc_data = cc.get_cross_channel_score(ca)
            if cc_data["channels"] >= 2:
                breakdown["cross_channel"] = cc_data["channels"]
                breakdown["cross_channel_sources"] = cc_data["sources"]
                breakdown["cross_channel_boost"] = round(cc_data["boost"] * 100, 1)
    except Exception:
        pass
    if lifecycle_data:
        breakdown["lifecycle_stages"] = lifecycle_data["stages"]
        breakdown["lifecycle_signals"] = lifecycle_data["num_signals"]

    return {
        "score": final_score,
        "verdict": verdict,
        "action": action,
        "breakdown": breakdown,
        "reason": reason,
        "dex_verified": dex_verified,
        "num_winners": len(winners),
        "num_losers": len(losers),
    }


def score_token_quick(token: dict) -> float:
    return score_token(token)["score"]
