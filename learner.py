"""
learner.py — Pattern-based signal system v2
- Track every launch for 6 hours
- 250k+ mcap = PUMP → learn pattern
- Below 150k = DUMP → learn to avoid
- 150k-250k = SKIP
- Pattern match → signal
- Dynamic criteria: learns optimal thresholds from pump vs dump patterns
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from config import config

logger = logging.getLogger("learner")

DATA_FILE = config.data_file

PUMP_THRESHOLD = 250000   # 250k mcap = pump
DUMP_THRESHOLD = 150000   # below 150k = dump
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

DEFAULT_SIGNAL_CRITERIA = {
    "min_bsr": 1.2,
    "min_holders": 3,
    "min_wallets": 3,
    "min_liq": 1500,
    "min_liq_pct": 3.0,
    "min_lp_locked": 0,
    "heuristic_threshold": 0.70,
    "pattern_threshold": 0.55,
    "max_age_seconds": 3600,
    "updated_at": None,
    "sample_size": 0,
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
    lp_locked = launch_data.lp_locked if hasattr(launch_data, 'lp_locked') else launch_data.get("lp_locked", 0)
    deployer = launch_data.deployer_wallet if hasattr(launch_data, 'deployer_wallet') else launch_data.get("deployer_wallet", "")
    ath_price = launch_data.ath_price if hasattr(launch_data, 'ath_price') else launch_data.get("ath_price", 0)

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
        "lp_locked": round(lp_locked, 1),
        "deployer": deployer[:12] if deployer else "",
        "ath_price": round(ath_price, 8),
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
    _compare("lp_locked", "lp_locked", 0.3)

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
        existing_features = existing[0].get("features", {})
        old_count = sum(1 for v in existing_features.values() if v not in (None, 0, 0.0, "", []))
        new_count = sum(1 for v in features.values() if v not in (None, 0, 0.0, "", []))
        if new_count > old_count:
            existing[0]["features"] = features
            launches[-500:] = launches
            data["launches_tracked"] = launches
            save_data(data)
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


def match_pump_patterns(features: dict, min_similarity: float = None) -> tuple[bool, float, str]:
    """Check if features match known pump patterns.
    Returns (match, score, reason)."""
    if min_similarity is None:
        criteria = get_signal_criteria()
        min_similarity = criteria.get("pattern_threshold", 0.55)

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


def record_signal_result(address: str, symbol: str, ath_multiplier: float, current_multiplier: float = 0.0) -> None:
    """Record signal outcome and learn from it."""
    data = load_data()
    results = data["model"].setdefault("signal_results", [])

    verdict = "DUMP"
    if ath_multiplier >= 5.0:
        verdict = "STRONG_PUMP"
    elif ath_multiplier >= 2.0:
        verdict = "PUMP"

    results.append({
        "address": address,
        "symbol": symbol,
        "verdict": verdict,
        "ath_multiplier": ath_multiplier,
        "current_multiplier": current_multiplier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["model"]["signal_results"] = results[-500:]

    launches = data.get("launches_tracked", [])
    launch = next((l for l in launches if l.get("address") == address), None)

    if launch and launch.get("features"):
        features = launch["features"]
        features["ath_multiplier"] = ath_multiplier
        features["outcome"] = verdict
        if verdict in ("PUMP", "STRONG_PUMP"):
            pump_patterns = data.setdefault("pump_patterns", [])
            pump_patterns.append({
                "symbol": symbol,
                "features": features,
                "outcome": verdict,
                "ath_multiplier": ath_multiplier,
                "learned_at": datetime.now(timezone.utc).isoformat(),
            })
            data["model"]["total_pumps"] = data["model"].get("total_pumps", 0) + 1
            logger.info(f"📚 পাম্প প্যাটার্ন শেখা: {symbol} (ATH {ath_multiplier:.1f}x)")
        elif verdict == "DUMP":
            dump_patterns = data.setdefault("dump_patterns", [])
            dump_patterns.append({
                "symbol": symbol,
                "features": features,
                "outcome": "DUMP",
                "ath_multiplier": ath_multiplier,
                "learned_at": datetime.now(timezone.utc).isoformat(),
            })
            data["model"]["total_dumps"] = data["model"].get("total_dumps", 0) + 1
            logger.info(f"📚 ডাম্প প্যাটার্ন শেখা: {symbol} (ATH {ath_multiplier:.1f}x)")

        if len(data.get("pump_patterns", [])) > MAX_PATTERNS:
            data["pump_patterns"] = data["pump_patterns"][-MAX_PATTERNS:]
        if len(data.get("dump_patterns", [])) > MAX_PATTERNS:
            data["dump_patterns"] = data["dump_patterns"][-MAX_PATTERNS:]

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
        "successful_signals": pumps,
        "strong_pumps": strong,
        "win_rate": round(pumps / total * 100, 1) if total > 0 else 0,
        "accuracy": round(pumps / total * 100, 1) if total > 0 else 0,
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
    missed = data.get("missed_pumps", [])
    missed_today = [m for m in missed if m.get("learned_at", "").startswith(today)]

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
        f"🚨 মিসড পাম্প: <b>{len(missed)}</b> (আজ: {len(missed_today)})\n"
    )

    insights = model.get("auto_learn_insights", {})
    if insights:
        report += (
            f"━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>Auto-Learn:</b>\n"
            f"  Win Rate: <b>{insights.get('win_rate', 0)}%</b>\n"
            f"  BSR: {insights.get('avg_win_bsr', 0)} vs {insights.get('avg_loss_bsr', 0)}\n"
            f"  Holders: {insights.get('avg_win_holders', 0)} vs {insights.get('avg_loss_holders', 0)}\n"
            f"  Liq: ${insights.get('avg_win_liq', 0)} vs ${insights.get('avg_loss_liq', 0)}\n"
            f"  LP Lock: {insights.get('avg_win_lp_locked', 0)}% vs {insights.get('avg_loss_lp_locked', 0)}%\n"
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


def record_missed_pump(address: str, symbol: str, features: dict, ath_multiplier: float) -> None:
    """Record a post-migration pump that wasn't caught by pre-migration signals.
    These are valuable training examples for what the bot missed."""
    data = load_data()
    missed = data.setdefault("missed_pumps", [])
    features["outcome"] = "missed_pump"
    features["ath_multiplier"] = ath_multiplier
    features["symbol"] = symbol
    features["address"] = address
    features["learned_at"] = datetime.now(timezone.utc).isoformat()
    missed.append(features)
    data["missed_pumps"] = missed[-200:]

    pump_patterns = data.setdefault("pump_patterns", [])
    pump_patterns.append(features)
    data["pump_patterns"] = pump_patterns[-MAX_PATTERNS:]

    data["model"]["total_pumps"] = data["model"].get("total_pumps", 0) + 1
    save_data(data)
    logger.info(f"📚 Missed pump learned: {symbol} ATH={ath_multiplier:.1f}x (post-migration)")


def auto_learn_update() -> dict:
    """Periodic auto-learn: analyze recent outcomes and adjust heuristic weights.
    Returns summary of what was learned."""
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    if len(results) < 10:
        return {"status": "insufficient_data", "count": len(results)}

    recent = results[-50:]
    wins = [r for r in recent if r.get("verdict") in ("PUMP", "STRONG_PUMP")]
    losses = [r for r in recent if r.get("verdict") == "DUMP"]

    if not wins and not losses:
        return {"status": "no_outcomes"}

    launches = data.get("launches_tracked", [])
    launch_map = {l.get("address"): l for l in launches}

    win_features = []
    loss_features = []
    for r in recent:
        launch = launch_map.get(r.get("address", ""))
        if not launch or not launch.get("features"):
            continue
        if r.get("verdict") in ("PUMP", "STRONG_PUMP"):
            win_features.append(launch["features"])
        elif r.get("verdict") == "DUMP":
            loss_features.append(launch["features"])

    if not win_features or not loss_features:
        return {"status": "insufficient_feature_data", "wins": len(win_features), "losses": len(loss_features)}

    def avg(lst, key):
        vals = [f.get(key, 0) for f in lst if f.get(key, 0) > 0]
        return sum(vals) / len(vals) if vals else 0

    insights = {
        "avg_win_bsr": round(avg(win_features, "buy_sell_ratio"), 2),
        "avg_loss_bsr": round(avg(loss_features, "buy_sell_ratio"), 2),
        "avg_win_holders": round(avg(win_features, "holders"), 1),
        "avg_loss_holders": round(avg(loss_features, "holders"), 1),
        "avg_win_wallets": round(avg(win_features, "unique_wallets"), 1),
        "avg_loss_wallets": round(avg(loss_features, "unique_wallets"), 1),
        "avg_win_liq": round(avg(win_features, "initial_liq"), 0),
        "avg_loss_liq": round(avg(loss_features, "initial_liq"), 0),
        "avg_win_mcap": round(avg(win_features, "initial_mcap"), 0),
        "avg_loss_mcap": round(avg(loss_features, "initial_mcap"), 0),
        "avg_win_lp_locked": round(avg(win_features, "lp_locked"), 1),
        "avg_loss_lp_locked": round(avg(loss_features, "lp_locked"), 1),
        "total_recent": len(recent),
        "win_rate": round(len(wins) / len(recent) * 100, 1),
    }

    data["model"]["last_auto_learn"] = datetime.now(timezone.utc).isoformat()
    data["model"]["auto_learn_insights"] = insights
    save_data(data)

    logger.info(f"🧠 Auto-learn: win_rate={insights['win_rate']}% "
                f"bsr: {insights['avg_win_bsr']} vs {insights['avg_loss_bsr']} "
                f"holders: {insights['avg_win_holders']} vs {insights['avg_loss_holders']} "
                f"liq: {insights['avg_win_liq']} vs {insights['avg_loss_liq']}")

    return insights


def compute_signal_criteria(min_patterns: int = 10) -> dict:
    """Analyze pump vs dump patterns and compute optimal signal criteria.
    Sets thresholds that best separate winning from losing features.
    Called after each learn cycle and 6h outcome check."""
    data = load_data()
    pump_patterns = data.get("pump_patterns", [])
    dump_patterns = data.get("dump_patterns", [])

    if len(pump_patterns) < min_patterns or len(dump_patterns) < min_patterns:
        criteria = dict(DEFAULT_SIGNAL_CRITERIA)
        criteria["updated_at"] = datetime.now(timezone.utc).isoformat()
        criteria["sample_size"] = len(pump_patterns) + len(dump_patterns)
        data["model"]["signal_criteria"] = criteria
        save_data(data)
        return criteria

    def _median(lst, key):
        vals = sorted([f.get(key, 0) for f in lst if f.get(key, 0) > 0])
        if not vals:
            return 0
        n = len(vals)
        if n % 2 == 1:
            return vals[n // 2]
        return (vals[n // 2 - 1] + vals[n // 2]) / 2

    def _pct_above(lst, key, threshold):
        vals = [f.get(key, 0) for f in lst if f.get(key, 0) > 0]
        if not vals:
            return 0
        return sum(1 for v in vals if v >= threshold) / len(vals) * 100

    features_to_analyze = [
        ("buy_sell_ratio", "min_bsr", "median"),
        ("holders", "min_holders", "median"),
        ("unique_wallets", "min_wallets", "median"),
        ("initial_liq", "min_liq", "median"),
        ("liq_pct", "min_liq_pct", "median"),
        ("lp_locked", "min_lp_locked", "median"),
    ]

    pump_medians = {}
    dump_medians = {}
    for feat_key, _, _ in features_to_analyze:
        pump_medians[feat_key] = _median(pump_patterns, feat_key)
        dump_medians[feat_key] = _median(dump_patterns, feat_key)

    criteria = {}
    for feat_key, criterion_key, method in features_to_analyze:
        p_med = pump_medians[feat_key]
        d_med = dump_medians[feat_key]

        if p_med > d_med:
            threshold = (p_med + d_med) / 2
        elif p_med > 0:
            threshold = p_med * 0.8
        else:
            threshold = DEFAULT_SIGNAL_CRITERIA.get(criterion_key, 0)

        criteria[criterion_key] = round(threshold, 2)

    p_bsr_above = _pct_above(pump_patterns, "buy_sell_ratio", 1.5)
    d_bsr_above = _pct_above(dump_patterns, "buy_sell_ratio", 1.5)
    if p_bsr_above > 60 and d_bsr_above < 40:
        criteria["min_bsr"] = max(criteria["min_bsr"], 1.3)

    p_holders_above = _pct_above(pump_patterns, "holders", 5)
    d_holders_above = _pct_above(dump_patterns, "holders", 5)
    if p_holders_above > 50 and d_holders_above < 30:
        criteria["min_holders"] = max(criteria["min_holders"], 4)

    p_liq_above = _pct_above(pump_patterns, "initial_liq", 3000)
    d_liq_above = _pct_above(dump_patterns, "initial_liq", 3000)
    if p_liq_above > 60 and d_liq_above < 30:
        criteria["min_liq"] = max(criteria["min_liq"], 2000)

    criteria["heuristic_threshold"] = 0.70
    criteria["pattern_threshold"] = 0.55
    criteria["max_age_seconds"] = 3600

    criteria["updated_at"] = datetime.now(timezone.utc).isoformat()
    criteria["sample_size"] = len(pump_patterns) + len(dump_patterns)

    data["model"]["signal_criteria"] = criteria
    data["model"]["signal_criteria_stats"] = {
        "pump_medians": {k: round(v, 2) for k, v in pump_medians.items()},
        "dump_medians": {k: round(v, 2) for k, v in dump_medians.items()},
        "pump_count": len(pump_patterns),
        "dump_count": len(dump_patterns),
    }
    save_data(data)

    logger.info(
        f"🎯 Signal criteria updated: "
        f"bsr≥{criteria['min_bsr']} holders≥{criteria['min_holders']} "
        f"wallets≥{criteria['min_wallets']} liq≥${int(criteria['min_liq'])} "
        f"liq%≥{criteria['min_liq_pct']} lp≥{criteria['min_lp_locked']}% "
        f"(from {len(pump_patterns)} pumps + {len(dump_patterns)} dumps)"
    )

    return criteria


def get_signal_criteria() -> dict:
    """Get current signal criteria, compute if not enough data."""
    data = load_data()
    criteria = data.get("model", {}).get("signal_criteria")
    if not criteria or not criteria.get("updated_at"):
        return compute_signal_criteria()
    return criteria
