"""
rug_detector.py — Detect rug pulls by monitoring LP changes after signal.

Post-signal monitoring:
1. Track LP at signal time vs current LP
2. If LP drops >50% within 1 hour → rug alert
3. Learn rug patterns from historical losers
4. Score tokens by rug probability before signal

Key rug indicators:
- Low LP relative to MCP (< 5%)
- Volume velocity spike (sudden activity = dump incoming)
- Buy/sell ratio approaching 1:1 (from 2:1)
- LP locked % suspicious (fake lock)
- Dev selling pattern
"""

import json
import os
import logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger("meme_bot.rug_detector")

RUG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rug_patterns.json")


def _load_rug_data():
    try:
        if os.path.exists(RUG_FILE):
            with open(RUG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"rugs": [], "patterns": {}, "lp_snapshots": {}}


def _save_rug_data(data):
    try:
        with open(RUG_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save rug data: {e}")


def snapshot_lp(ca: str, token_data: dict):
    """
    Take LP snapshot at signal time.
    Called when a signal is first detected.
    """
    rug_data = _load_rug_data()
    snapshots = rug_data.setdefault("lp_snapshots", {})

    now = datetime.now(timezone.utc).timestamp()
    liq = token_data.get("liq_usd", 0)
    mcp = token_data.get("mcp", 0) or token_data.get("launch_mcp", 0)

    if ca not in snapshots:
        snapshots[ca] = {
            "symbol": token_data.get("symbol", "?"),
            "signal_time": now,
            "signal_liq": liq,
            "signal_mcp": mcp,
            "signal_holders": token_data.get("holders", 0),
            "signal_buy_sell_ratio": 0,
            "signal_volume_5m": token_data.get("volume_5m", 0),
            "signal_price_change_5m": token_data.get("price_change_5m", 0),
            "signal_dev_status": token_data.get("dev_status", "UNKNOWN"),
            "signal_top10_pct": token_data.get("top10_pct", 0),
            "signal_txns_5m": token_data.get("txns_5m", 0),
            "signal_signal_type": token_data.get("signal_type", "?"),
            "lp_history": [(now, liq)],
            "is_rug": False,
            "rug_detected_at": None,
        }

    _save_rug_data(rug_data)


def update_lp(ca: str, current_liq: float, current_mcp: float = 0):
    """
    Update LP snapshot. Called during post-signal monitoring.
    Returns: {"is_rug": bool, "lp_drop_pct": float, "reason": str}
    """
    rug_data = _load_rug_data()
    snapshots = rug_data.get("lp_snapshots", {})

    if ca not in snapshots:
        return {"is_rug": False, "lp_drop_pct": 0, "reason": "no snapshot"}

    snap = snapshots[ca]
    now = datetime.now(timezone.utc).timestamp()
    signal_liq = snap["signal_liq"]

    # Add to history
    snap["lp_history"].append((now, current_liq))
    # Keep only last 100 snapshots
    if len(snap["lp_history"]) > 100:
        snap["lp_history"] = snap["lp_history"][-100:]

    # Check for rug
    if signal_liq > 0:
        lp_drop = 1.0 - (current_liq / signal_liq)
    else:
        lp_drop = 1.0 if current_liq <= 0 else 0

    is_rug = False
    reason = ""

    # RUG DETECTION: LP dropped >50% from signal time
    if lp_drop > 0.50 and signal_liq > 100:
        is_rug = True
        reason = f"LP dropped {lp_drop:.0%} (${signal_liq:,.0f}→${current_liq:,.0f})"
        if not snap.get("is_rug"):
            snap["is_rug"] = True
            snap["rug_detected_at"] = now
            logger.warning(f"[RUG DETECTED] {snap['symbol']} CA={ca[:12]}... {reason}")

    # RUG DETECTION: LP near zero
    if current_liq < 100 and signal_liq > 1000:
        is_rug = True
        reason = f"LP near zero (${current_liq:.0f})"
        if not snap.get("is_rug"):
            snap["is_rug"] = True
            snap["rug_detected_at"] = now
            logger.warning(f"[RUG DETECTED] {snap['symbol']} CA={ca[:12]}... {reason}")

    # Record rug pattern for learning
    if is_rug and not snap.get("pattern_recorded"):
        snap["pattern_recorded"] = True
        record_rug_pattern(ca, snap)

    _save_rug_data(rug_data)

    return {"is_rug": is_rug, "lp_drop_pct": lp_drop, "reason": reason}


def record_rug_pattern(ca: str, snap: dict):
    """Record rug pattern for learning."""
    rug_data = _load_rug_data()
    patterns = rug_data.setdefault("patterns", {})

    # Extract features that predict this rug
    features = {
        "signal_liq": snap.get("signal_liq", 0),
        "signal_mcp": snap.get("signal_mcp", 0),
        "signal_holders": snap.get("signal_holders", 0),
        "signal_volume_5m": snap.get("signal_volume_5m", 0),
        "signal_price_change_5m": snap.get("signal_price_change_5m", 0),
        "signal_dev_status": snap.get("signal_dev_status", "UNKNOWN"),
        "signal_top10_pct": snap.get("signal_top10_pct", 0),
        "signal_txns_5m": snap.get("signal_txns_5m", 0),
        "signal_signal_type": snap.get("signal_signal_type", "?"),
        "lp_mcp_ratio": snap.get("signal_liq", 0) / max(snap.get("signal_mcp", 1), 1),
    }

    rug_entry = {
        "ca": ca,
        "symbol": snap.get("symbol", "?"),
        "features": features,
        "rug_time": snap.get("rug_detected_at", 0),
        "signal_time": snap.get("signal_time", 0),
        "time_to_rug": (snap.get("rug_detected_at", 0) - snap.get("signal_time", 0)) if snap.get("rug_detected_at") else 0,
    }

    rugs = rug_data.setdefault("rugs", [])
    rugs.append(rug_entry)
    # Keep only last 500
    if len(rugs) > 500:
        rug_data["rugs"] = rugs[-500:]

    logger.info(f"[RUG PATTERN] {snap.get('symbol', '?')} recorded: liq={features['signal_liq']:.0f} mcp={features['signal_mcp']:.0f} holders={features['signal_holders']} dev={features['signal_dev_status']}")

    _save_rug_data(rug_data)


def get_rug_probability(token_data: dict) -> dict:
    """
    Calculate rug probability based on learned patterns.
    Returns: {"rug_prob": float (0-1), "reasons": list}
    """
    rug_data = _load_rug_data()
    rugs = rug_data.get("rugs", [])

    if not rugs:
        return {"rug_prob": 0.3, "reasons": ["no rug data yet"]}

    reasons = []
    rug_prob = 0.0

    liq = token_data.get("liq_usd", 0)
    mcp = token_data.get("mcp", 0) or token_data.get("launch_mcp", 0)
    holders = token_data.get("holders", 0)
    dev_status = token_data.get("dev_status", "UNKNOWN")
    top10_pct = token_data.get("top10_pct", 0)
    volume_5m = token_data.get("volume_5m", 0)
    price_change_5m = token_data.get("price_change_5m", 0)
    signal_type = token_data.get("signal_type", "?")

    # 1. LP/MCP ratio check — too LOW LP relative to MCP = rug risk
    if mcp > 0 and liq > 0:
        lp_ratio = liq / mcp
        if lp_ratio < 0.01:
            rug_prob += 0.25
            reasons.append(f"LP/MCP ratio extremely low ({lp_ratio:.2f})")
        elif lp_ratio < 0.05:
            rug_prob += 0.15
            reasons.append(f"LP/MCP ratio low ({lp_ratio:.2f})")
    elif mcp > 0 and liq == 0:
        rug_prob += 0.30
        reasons.append("Zero LP")

    # 2. Low holders check
    rug_holders = [r["features"]["signal_holders"] for r in rugs if r["features"]["signal_holders"] > 0]
    if rug_holders and holders > 0:
        avg_rug_holders = sum(rug_holders) / len(rug_holders)
        if holders < avg_rug_holders * 0.5:
            rug_prob += 0.15
            reasons.append(f"Holders {holders} < rug avg {avg_rug_holders:.0f}")

    # 3. Dev status check
    rug_devs = [r["features"]["signal_dev_status"] for r in rugs]
    from collections import Counter
    dev_counts = Counter(rug_devs)
    if dev_status in ("SELL_ALL", "UNKNOWN"):
        sell_all_rate = dev_counts.get("SELL_ALL", 0) / max(len(rugs), 1)
        unknown_rate = dev_counts.get("UNKNOWN", 0) / max(len(rugs), 1)
        if dev_status == "SELL_ALL" and sell_all_rate > 0.1:
            rug_prob += 0.2
            reasons.append(f"DEV_SOLD (rug rate {sell_all_rate:.0%})")
        elif dev_status == "UNKNOWN" and unknown_rate > 0.3:
            rug_prob += 0.1
            reasons.append(f"DEV_UNKNOWN (common in rugs)")

    # 4. Top 10 concentration
    if top10_pct > 50:
        rug_prob += 0.15
        reasons.append(f"Top 10 hold {top10_pct:.0f}% — concentrated")

    # 5. Volume spike without price movement (dump signal)
    if volume_5m > 0 and price_change_5m == 0:
        rug_prob += 0.1
        reasons.append("Volume but no price change — possible dump")

    # Cap at 0.95
    rug_prob = min(rug_prob, 0.95)

    return {
        "rug_prob": rug_prob,
        "reasons": reasons if reasons else ["no strong rug signals"],
    }


def get_rug_stats():
    """Get rug pattern statistics."""
    rug_data = _load_rug_data()
    rugs = rug_data.get("rugs", [])
    return {
        "total_rugs": len(rugs),
        "avg_time_to_rug": sum(r.get("time_to_rug", 0) for r in rugs) / max(len(rugs), 1),
        "avg_signal_liq": sum(r["features"]["signal_liq"] for r in rugs) / max(len(rugs), 1),
        "avg_signal_holders": sum(r["features"]["signal_holders"] for r in rugs) / max(len(rugs), 1),
    }
