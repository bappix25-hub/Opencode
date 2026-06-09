"""
learner.py — Pattern-based signal system v2
- Track every launch for 6 hours
- 400k+ mcap = PUMP → learn pattern
- Below 300k = DUMP → learn to avoid
- 300k-400k = SKIP
- Pattern match → signal
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from config import config

logger = logging.getLogger("learner")

DATA_FILE = config.data_file

PUMP_THRESHOLD = 400000   # 400k mcap = pump
DUMP_THRESHOLD = 300000   # below 300k = dump
OUTCOME_WINDOW = 21600    # 6 hours
MAX_PATTERNS = 200        # keep top 200 pump patterns

DEFAULT_DATA = {
    "pump_patterns": [],
    "dump_patterns": [],
    "signal_results": [],
    "launches_tracked": [],
    "model": {
        "total_pumps": 0,
        "total_dumps": 0,
        "total_skipped": 0,
        "last_update": None,
    }
}


def load_data() -> dict:
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
        for k, v in DEFAULT_DATA.items():
            if k not in data:
                data[k] = v
        if "model" not in data:
            data["model"] = DEFAULT_DATA["model"]
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_data error: {e}")


def _hash_address(address: str) -> str:
    import hashlib
    return hashlib.md5(address.encode()).hexdigest()[:12]


def extract_launch_features(launch_data, pair_data=None, unique_wallets=0) -> dict:
    """Extract early-stage features for pattern matching."""
    buy_count = launch_data.buy_count if hasattr(launch_data, 'buy_count') else launch_data.get("buy_count", 0)
    sell_count = launch_data.sell_count if hasattr(launch_data, 'sell_count') else launch_data.get("sell_count", 0)
    volume = launch_data.volume if hasattr(launch_data, 'volume') else launch_data.get("volume", 0)
    holders = launch_data.holders if hasattr(launch_data, 'holders') else launch_data.get("holders", 0)
    launch_time = launch_data.launch_time if hasattr(launch_data, 'launch_time') else launch_data.get("launch_time", 0)

    buy_sell_ratio = buy_count / max(sell_count, 1)

    initial_liq = 0
    initial_mcap = 0
    liq_pct = 0
    snipers_30s = 0
    insiders_30s = 0

    if pair_data:
        initial_liq = pair_data.get("liquidity", {}).get("usd", 0) if isinstance(pair_data.get("liquidity"), dict) else 0
        initial_mcap = pair_data.get("marketCap", 0) or pair_data.get("fdv", 0)
        if initial_mcap > 0 and initial_liq > 0:
            liq_pct = initial_liq / initial_mcap * 100

    if hasattr(launch_data, 'buy_timestamps') and launch_data.buy_timestamps:
        launch_ts = launch_time
        first_30s = [t for t in launch_data.buy_timestamps if t - launch_ts <= 30]
        snipers_30s = len(first_30s)
    elif isinstance(launch_data, dict) and "buy_timestamps" in launch_data:
        launch_ts = launch_time
        first_30s = [t for t in launch_data.get("buy_timestamps", []) if t - launch_ts <= 30]
        snipers_30s = len(first_30s)

    launch_hour = datetime.fromtimestamp(launch_time, tz=timezone.utc).hour if launch_time else 0

    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "volume": round(volume, 2),
        "holders": holders,
        "unique_wallets": unique_wallets,
        "initial_liq": round(initial_liq, 2),
        "initial_mcap": round(initial_mcap, 2),
        "liq_pct": round(liq_pct, 2),
        "snipers_30s": snipers_30s,
        "insiders_30s": insiders_30s,
        "launch_hour": launch_hour,
    }


def _pattern_similarity(features: dict, pattern: dict) -> float:
    """Calculate similarity between features and a known pattern. 0.0 to 1.0."""
    score = 0.0
    checks = 0

    def _compare(feat_key, pat_key, tolerance=0.5):
        nonlocal score, checks
        f_val = features.get(feat_key, 0)
        p_val = pattern.get(pat_key, 0)
        if p_val == 0 and f_val == 0:
            score += 1.0
            checks += 1
            return
        if p_val == 0:
            checks += 1
            return
        ratio = min(f_val, p_val) / max(f_val, p_val) if max(f_val, p_val) > 0 else 0
        score += ratio
        checks += 1

    _compare("buy_sell_ratio", "buy_sell_ratio", 0.3)
    _compare("holders", "holders", 0.5)
    _compare("unique_wallets", "unique_wallets", 0.3)
    _compare("snipers_30s", "snipers_30s", 0.3)

    if features.get("liq_pct", 0) > 0 and pattern.get("liq_pct", 0) > 0:
        ratio = min(features["liq_pct"], pattern["liq_pct"]) / max(features["liq_pct"], pattern["liq_pct"])
        score += ratio
        checks += 1

    if features.get("launch_hour", 0) == pattern.get("launch_hour", 0):
        score += 1.0
        checks += 1
    elif abs(features.get("launch_hour", 0) - pattern.get("launch_hour", 0)) <= 2:
        score += 0.5
        checks += 1

    return score / checks if checks > 0 else 0.0


def record_launch(address: str, symbol: str, features: dict) -> None:
    """Record a new launch for tracking. Check outcome after 6h."""
    data = load_data()
    launches = data.setdefault("launches_tracked", [])

    existing = [l for l in launches if l.get("address") == address]
    if existing:
        return

    launches.append({
        "address": address,
        "symbol": symbol,
        "features": features,
        "launch_time": features.get("launch_time", datetime.now(timezone.utc).timestamp()),
        "outcome": None,
        "outcome_mcap": 0,
        "outcome_time": None,
        "signal_sent": False,
    })

    launches = launches[-500:]
    data["launches_tracked"] = launches
    save_data(data)


def check_and_record_outcome(address: str, current_mcap: float) -> Optional[str]:
    """Check if a tracked launch has reached its outcome window.
    Returns 'pump', 'dump', 'skip', or None (still tracking)."""
    data = load_data()
    launches = data.get("launches_tracked", [])

    for launch in launches:
        if launch.get("address") != address:
            continue
        if launch.get("outcome") is not None:
            continue

        launch_time = launch.get("launch_time", 0)
        age = datetime.now(timezone.utc).timestamp() - launch_time

        if age < OUTCOME_WINDOW:
            return None

        if current_mcap >= PUMP_THRESHOLD:
            launch["outcome"] = "pump"
            launch["outcome_mcap"] = current_mcap
            launch["outcome_time"] = datetime.now(timezone.utc).isoformat()
            _learn_pump(launch)
            save_data(data)
            return "pump"
        elif current_mcap < DUMP_THRESHOLD:
            launch["outcome"] = "dump"
            launch["outcome_mcap"] = current_mcap
            launch["outcome_time"] = datetime.now(timezone.utc).isoformat()
            _learn_dump(launch)
            save_data(data)
            return "dump"
        else:
            launch["outcome"] = "skip"
            launch["outcome_mcap"] = current_mcap
            launch["outcome_time"] = datetime.now(timezone.utc).isoformat()
            data["model"]["total_skipped"] = data["model"].get("total_skipped", 0) + 1
            save_data(data)
            return "skip"

    return None


def _learn_pump(launch: dict) -> None:
    """Learn from a pump outcome."""
    data = load_data()
    features = launch.get("features", {})
    features["outcome"] = "pump"
    features["outcome_mcap"] = launch.get("outcome_mcap", 0)
    features["symbol"] = launch.get("symbol", "?")
    features["address"] = launch.get("address", "")

    patterns = data.setdefault("pump_patterns", [])
    patterns.append(features)
    patterns = patterns[-MAX_PATTERNS:]
    data["pump_patterns"] = patterns

    data["model"]["total_pumps"] = data["model"].get("total_pumps", 0) + 1
    data["model"]["last_update"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"🟢 LEARNED PUMP: {launch.get('symbol')} mcap={launch.get('outcome_mcap', 0):.0f} "
                f"buy_sell={features.get('buy_sell_ratio')} holders={features.get('holders')} "
                f"liq%={features.get('liq_pct')} snipers={features.get('snipers_30s')}")


def _learn_dump(launch: dict) -> None:
    """Learn from a dump outcome."""
    data = load_data()
    features = launch.get("features", {})
    features["outcome"] = "dump"
    features["outcome_mcap"] = launch.get("outcome_mcap", 0)
    features["symbol"] = launch.get("symbol", "?")

    patterns = data.setdefault("dump_patterns", [])
    patterns.append(features)
    patterns = patterns[-MAX_PATTERNS:]
    data["dump_patterns"] = patterns

    data["model"]["total_dumps"] = data["model"].get("total_dumps", 0) + 1
    data["model"]["last_update"] = datetime.now(timezone.utc).isoformat()


def match_pump_patterns(features: dict, min_similarity: float = 0.55) -> tuple[bool, float, str]:
    """Check if features match known pump patterns.
    Returns (match, score, reason)."""
    data = load_data()
    pump_patterns = data.get("pump_patterns", [])

    if not pump_patterns:
        return False, 0.0, "No pump patterns learned yet"

    best_score = 0.0
    best_match = None

    for pattern in pump_patterns:
        sim = _pattern_similarity(features, pattern)
        if sim > best_score:
            best_score = sim
            best_match = pattern

    if best_score >= min_similarity:
        reasons = []
        if features.get("buy_sell_ratio", 0) >= 1.5:
            reasons.append(f"buy_sell={features['buy_sell_ratio']:.1f}")
        if features.get("holders", 0) >= 3:
            reasons.append(f"holders={features['holders']}")
        if features.get("snipers_30s", 0) >= 2:
            reasons.append(f"snipers={features['snipers_30s']}")
        if features.get("liq_pct", 0) >= 5:
            reasons.append(f"liq%={features['liq_pct']:.1f}%")

        reason = f"Matched {best_match.get('symbol', '?')} ({best_score:.0%}) " + " ".join(reasons)
        return True, best_score, reason

    return False, best_score, f"Best match {best_score:.0%} < {min_similarity:.0%}"


def record_signal_result(address: str, symbol: str, multiplier: float) -> None:
    """Record signal outcome for stats."""
    data = load_data()
    results = data["model"].setdefault("signal_results", [])

    verdict = "DUMP"
    if multiplier >= 5.0:
        verdict = "STRONG_PUMP"
    elif multiplier >= 2.0:
        verdict = "PUMP"

    results.append({
        "address": address,
        "symbol": symbol,
        "verdict": verdict,
        "multiplier": multiplier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["model"]["signal_results"] = results[-500:]
    save_data(data)


def get_stats() -> dict:
    """Get current learning stats."""
    data = load_data()
    model = data.get("model", {})
    results = model.get("signal_results", [])
    total = len(results)
    pumps = sum(1 for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP"))
    strong = sum(1 for r in results if r.get("verdict") == "STRONG_PUMP")

    return {
        "total_pumps": model.get("total_pumps", 0),
        "total_dumps": model.get("total_dumps", 0),
        "total_skipped": model.get("total_skipped", 0),
        "pump_patterns": len(data.get("pump_patterns", [])),
        "dump_patterns": len(data.get("dump_patterns", [])),
        "total_signals": total,
        "successful": pumps,
        "strong_pumps": strong,
        "win_rate": round(pumps / total * 100, 1) if total > 0 else 0,
    }


def get_daily_report() -> str:
    """Generate daily learning summary."""
    data = load_data()
    model = data.get("model", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    launches_today = [
        l for l in data.get("launches_tracked", [])
        if l.get("launch_time", 0) > (datetime.now(timezone.utc).timestamp() - 86400)
    ]
    pumps_today = [l for l in launches_today if l.get("outcome") == "pump"]
    dumps_today = [l for l in launches_today if l.get("outcome") == "dump"]
    pending = [l for l in launches_today if l.get("outcome") is None]

    report = (
        f"📊 <b>দৈনিক রিপোর্ট — {today}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 ট্র্যাক করা: <b>{len(launches_today)}</b>\n"
        f"🟢 পাম্প: <b>{len(pumps_today)}</b>\n"
        f"🔴 ডাম্প: <b>{len(dumps_today)}</b>\n"
        f"⏳ পেন্ডিং: <b>{len(pending)}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📈 মোট পাম্প প্যাটার্ন: <b>{model.get('total_pumps', 0)}</b>\n"
        f"📉 মোট ডাম্প প্যাটার্ন: <b>{model.get('total_dumps', 0)}</b>\n"
    )

    if pumps_today:
        pump_syms = ", ".join([l.get("symbol", "?") for l in pumps_today[:10]])
        report += f"🟢 আজকের পাম্প: <b>{pump_syms}</b>\n"

    return report


def get_launch_age(pair: dict) -> Optional[float]:
    """Get launch age in seconds from pair data."""
    try:
        created_at = pair.get("pairCreatedAt")
        if created_at:
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            return (now_ms - int(created_at)) / 1000
    except Exception:
        pass
    return None


def is_duplicate(address: str) -> bool:
    """Check if address already processed."""
    data = load_data()
    for l in data.get("launches_tracked", []):
        if l.get("address") == address:
            return True
    return False


def purge_honeypot_patterns(honeypot_set: set) -> dict:
    """Remove honeypot addresses from pump patterns."""
    data = load_data()
    before = len(data.get("pump_patterns", []))
    data["pump_patterns"] = [
        p for p in data.get("pump_patterns", [])
        if p.get("address") not in honeypot_set
    ]
    after = len(data["pump_patterns"])
    save_data(data)
    return {"moved": before - after}


def save_honeypot_blocklist(addr_set: set, deployer_set: set) -> None:
    """Save honeypot blocklist."""
    data = load_data()
    data["honeypot_addresses"] = list(addr_set)
    data["blocked_deployers"] = list(deployer_set)
    save_data(data)


def load_honeypot_blocklist() -> tuple[set, set]:
    """Load honeypot blocklist."""
    data = load_data()
    return set(data.get("honeypot_addresses", [])), set(data.get("blocked_deployers", []))
