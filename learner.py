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
MAX_PUMP_PATTERNS = 500   # keep top 500 pump patterns
MAX_DUMP_PATTERNS = 1000  # keep top 1000 dump patterns (more diverse)

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
    "min_bsr": 1.7,
    "min_holders": 50,
    "min_wallets": 15,
    "min_liq": 500,
    "min_liq_pct": 10,
    "min_lp_locked": 80,
    "min_mcap": 3000,
    "heuristic_threshold": 0.45,
    "pattern_threshold": 0.60,
    "dump_pattern_threshold": 0.70,
    "max_age_seconds": 21600,
    "updated_at": None,
    "sample_size": 0,
    "min_lp_providers": 2,
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


def calculate_pattern_strength(signals):
    """Calculate pattern strength based on various features.
    Returns a score between 0 and 1.
    """
    if not signals:
        return 0.5
    
    scores = []
    now = datetime.now(timezone.utc).timestamp()
    for signal in signals:
        score = 0
        features = extract_launch_features(signal)
        
        # Buy/sell ratio (higher is better)
        buy_sell_ratio = features.get("buy_sell_ratio", 0)
        if buy_sell_ratio > 2:
            score += 0.25
        elif buy_sell_ratio > 1.5:
            score += 0.15
        
        # Holders count (higher is better)
        holders = features.get("holders", 0)
        if holders >= 10:
            score += 0.25
        elif holders >= 5:
            score += 0.15
        
        # Liquidity percentage (higher is better)
        liq_pct = features.get("liq_pct", 0)
        if liq_pct > 20:
            score += 0.25
        elif liq_pct > 10:
            score += 0.15
        
        # Token age (newer is better) - calculate age from launch_time
        launch_time = features.get("launch_time", 0)
        if launch_time > 0:
            age_seconds = now - launch_time
            if age_seconds < 300:  # Less than 5 minutes old
                score += 0.15
            elif age_seconds < 600:  # Less than 10 minutes old
                score += 0.08
        
        scores.append(score)
    
    return sum(scores) / len(scores) if scores else 0.5


def calculate_win_rate(results):
    """Calculate win rate from results."""
    if not results:
        return 0
    
    wins = sum(1 for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP"))
    return wins / len(results)


def calculate_average_pnl(results):
    """Calculate average PnL from results."""
    if not results:
        return 0
    
    total_pnl = sum(r.get("pnl", 0) for r in results)
    return total_pnl / len(results)


def calculate_average_win(results):
    """Calculate average win from results."""
    wins = [r["pnl"] for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP")]
    return sum(wins) / len(wins) if wins else 0


def calculate_average_loss(results):
    """Calculate average loss from results."""
    losses = [r["pnl"] for r in results if r.get("verdict") == "DUMP"]
    return sum(losses) / len(losses) if losses else 0


def calculate_var(returns, confidence=0.95):
    """Calculate Value at Risk (VaR) from returns."""
    if not returns:
        return 0
    
    sorted_returns = sorted(returns)
    index = int(len(sorted_returns) * (1 - confidence))
    return sorted_returns[index]


def calculate_max_drawdown(signals):
    """Calculate maximum drawdown from signals."""
    if not signals:
        return 0
    
    max_dd = 0
    peak = float('-inf')
    
    for signal in sorted(signals, key=lambda x: x.get("timestamp", "")):
        pnl = signal.get("pnl", 0)
        if pnl > peak:
            peak = pnl
        else:
            drawdown = (peak - pnl) / peak * 100
            max_dd = max(max_dd, drawdown)
    
    return max_dd


def calculate_sharpe_ratio(signals):
    """Calculate Sharpe ratio from signals."""
    if not signals:
        return 0
    
    returns = [s.get("pnl", 0) for s in signals]
    avg_return = sum(returns) / len(returns)
    variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
    std_dev = variance ** 0.5
    
    return avg_return / std_dev if std_dev > 0 else 0


def calculate_average_ath(results):
    """Calculate average ATH multiplier from results."""
    aths = [r.get("ath_multiplier", 1) for r in results if r.get("ath_multiplier", 0) > 0]
    return sum(aths) / len(aths) if aths else 1


def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_data error: {e}")


def calculate_signal_quality_score(recent_signals):
    """Calculate a comprehensive signal quality score.
    Combines win rate, average profit, pattern strength, and recent trends.
    """
    if not recent_signals:
        return 0.5
    
    # Calculate win rate
    wins = [s for s in recent_signals if s.get("verdict") in ("PUMP", "STRONG_PUMP")]
    win_rate = len(wins) / len(recent_signals) if recent_signals else 0
    
    # Calculate average PnL
    total_pnl = sum(s.get("pnl", 0) for s in recent_signals)
    avg_pnl = total_pnl / len(recent_signals) if recent_signals else 0
    
    # Calculate pattern strength
    pattern_strength = calculate_pattern_strength(recent_signals)
    
    # Calculate recent trend (weighted average of last 10 signals)
    recent_10 = recent_signals[-10:] if len(recent_signals) >= 10 else recent_signals
    recent_score = calculate_pattern_strength(recent_10)
    
    # Combine with weights
    quality_score = (
        win_rate * 0.3 +           # Historical accuracy (30%)
        min(max(avg_pnl / 50, 0), 1) * 0.2 +  # Average profit (20%, capped at 50% PnL)
        pattern_strength * 0.3 +    # Pattern strength (30%)
        recent_score * 0.2         # Recent trend (20%)
    )
    
    return min(max(quality_score, 0.35), 0.75)  # Clamp 0.35-0.75


def calculate_advanced_tp_sl(results):
    """Calculate TP/SL using risk management metrics like Kelly Criterion and VaR.
    """
    if not results:
        return {"optimal_tp": 100, "optimal_sl": -15, "expected_pnl": 0}
    
    # Calculate risk metrics
    returns = [r.get("pnl", 0) for r in results]
    avg_return = sum(returns) / len(returns)
    variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
    std_dev = variance ** 0.5
    
    # Calculate Sharpe Ratio
    sharpe_ratio = avg_return / std_dev if std_dev > 0 else 0
    
    # Calculate Kelly Criterion for position sizing
    win_rate = sum(1 for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP")) / len(results)
    wins = [r.get("pnl", 0) for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP")]
    losses = [r.get("pnl", 0) for r in results if r.get("verdict") == "DUMP"]
    
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    
    if avg_loss < 0:
        kelly_fraction = (win_rate * avg_win - abs(avg_loss)) / avg_win
    else:
        kelly_fraction = 0
    
    kelly_fraction = max(0.2, min(0.8, kelly_fraction))  # Clamp 20-80%
    
    # Calculate Value at Risk (VaR)
    sorted_returns = sorted(returns)
    var_95 = sorted_returns[int(len(sorted_returns) * 0.05)]  # 5% VaR
    var_99 = sorted_returns[int(len(sorted_returns) * 0.01)]  # 1% VaR
    
    # Find best TP/SL based on multiple criteria
    best_combo = None
    best_score = -999
    
    for tp_pct in range(50, 301, 25):
        for sl_pct in range(-30, 0, 5):
            # Calculate risk-adjusted return
            risk_adj_return = (avg_return / abs(sl_pct)) if sl_pct < 0 else 0
            sharpe_score = sharpe_ratio * (tp_pct / 100)
            kelly_score = kelly_fraction * (tp_pct / 100)
            
            # Combine scores
            total_score = (
                risk_adj_return * 0.4 +    # Risk-adjusted return (40%)
                sharpe_score * 0.3 +       # Sharpe ratio (30%)
                kelly_score * 0.3          # Kelly fraction (30%)
            )
            
            if total_score > best_score:
                best_score = total_score
                best_combo = {
                    "optimal_tp": tp_pct,
                    "optimal_sl": sl_pct,
                    "expected_pnl": avg_return,
                    "sharpe_ratio": sharpe_ratio,
                    "kelly_fraction": kelly_fraction,
                    "var_95": var_95,
                    "var_99": var_99,
                    "risk_adj_return": risk_adj_return
                }
    
    return best_combo or {"optimal_tp": 100, "optimal_sl": -15, "expected_pnl": avg_return}


def enhanced_auto_learn():
    """Enhanced auto-learn that considers multiple performance metrics.
    """
    from datetime import datetime, timezone
    
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    
    if len(results) < 10:
        return {"status": "insufficient_data", "count": len(results)}
    
    # Get last 50 signals for analysis
    recent_signals = results[-50:] if len(results) >= 50 else results
    
    # Calculate multiple metrics
    metrics = {
        "win_rate": sum(1 for s in recent_signals if s.get("verdict") in ("PUMP", "STRONG_PUMP")) / len(recent_signals),
        "avg_pnl": sum(s.get("pnl", 0) for s in recent_signals) / len(recent_signals),
        "pattern_strength": calculate_pattern_strength(recent_signals),
        "recent_trend": calculate_pattern_strength(recent_signals[-10:]) if len(recent_signals) >= 10 else 0,
        "dump_rate": sum(1 for s in recent_signals if s.get("verdict") == "DUMP") / len(recent_signals),
        "avg_ath": sum(s.get("ath_multiplier", 1) for s in recent_signals) / len(recent_signals),
    }
    
    # Calculate overall quality score
    quality_score = calculate_signal_quality_score(recent_signals)
    
    # Determine new heuristic_threshold based on comprehensive analysis
    current_threshold = data.get("model", {}).get("signal_criteria", {}).get("heuristic_threshold", 0.45)
    current_pattern_threshold = data.get("model", {}).get("signal_criteria", {}).get("pattern_threshold", 0.55)
    
    # Only TIGHTEN thresholds on failure, NEVER relax on success
    if quality_score < 0.45:
        new_threshold = min(0.60, current_threshold + 0.08)
    elif quality_score < 0.50:
        new_threshold = min(0.55, current_threshold + 0.04)
    elif metrics["win_rate"] < 0.15:
        new_threshold = min(0.55, current_threshold + 0.03)
    else:
        new_threshold = current_threshold  # Never lower
    
    # Adjust pattern_threshold based on dump rate (only tighten, never relax)
    if metrics["dump_rate"] > 0.35:
        new_pattern_threshold = min(0.85, current_pattern_threshold + 0.03)
    elif metrics["dump_rate"] > 0.25:
        new_pattern_threshold = min(0.82, current_pattern_threshold + 0.02)
    else:
        new_pattern_threshold = current_pattern_threshold  # Never lower
    
    # Adjust volatility setting based on average ATH
    if metrics["avg_ath"] > 3.5:
        volatility_setting = "high"
    elif metrics["avg_ath"] < 2.0:
        volatility_setting = "low"
    else:
        volatility_setting = "medium"
    
    # Update the criteria
    criteria_data = data.get("model", {}).get("signal_criteria", {})
    criteria_data["heuristic_threshold"] = round(new_threshold, 2)
    criteria_data["pattern_threshold"] = round(new_pattern_threshold, 2)
    criteria_data["volatility_setting"] = volatility_setting
    criteria_data["last_quality_score"] = round(quality_score, 2)
    data["model"]["signal_criteria"] = criteria_data
    
    # Update auto_learn_insights for daily reports
    data["model"]["auto_learn_insights"] = {
        "win_rate": round(metrics["win_rate"] * 100, 1),
        "avg_win_bsr": round(metrics.get("avg_win_bsr", 0), 2),
        "avg_loss_bsr": round(metrics.get("avg_loss_bsr", 0), 2),
        "avg_win_holders": round(metrics.get("avg_win_holders", 0), 0),
        "avg_loss_holders": round(metrics.get("avg_loss_holders", 0), 0),
        "quality_score": round(quality_score, 2),
        "dump_rate": round(metrics["dump_rate"] * 100, 1),
        "avg_ath": round(metrics["avg_ath"], 2),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    
    save_data(data)
    
    return {
        "heuristic_threshold": round(new_threshold, 2),
        "pattern_threshold": round(new_pattern_threshold, 2),
        "volatility_setting": volatility_setting,
        "quality_score": round(quality_score, 2),
        "metrics": metrics,
    }


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

    # Insider detection: wallets that buy very early (first 10s) with large amounts
    insider_wallets = set()
    if hasattr(launch_data, 'buy_timestamps') and launch_data.buy_timestamps:
        launch_ts = launch_time
        first_10s = [(t, w) for t, w in zip(launch_data.buy_timestamps, launch_data.buy_wallets) if t - launch_ts <= 10] if hasattr(launch_data, 'buy_wallets') else []
        for _, wallet in first_10s:
            insider_wallets.add(wallet)
        insiders_30s = len(insider_wallets)

    # Time features
    launch_dt = datetime.fromtimestamp(launch_time, tz=timezone.utc) if launch_time else datetime.now(timezone.utc)
    launch_hour = launch_dt.hour
    launch_weekday = launch_dt.weekday()  # 0=Mon, 6=Sun
    launch_month = launch_dt.month
    is_weekend = launch_weekday >= 5  # Sat=5, Sun=6

    # Session: asian (0-8), european (8-16), us (16-24)
    if 0 <= launch_hour < 8:
        launch_session = "asian"
    elif 8 <= launch_hour < 16:
        launch_session = "european"
    else:
        launch_session = "us"

    # Derived features
    age_seconds = datetime.now(timezone.utc).timestamp() - launch_time if launch_time else 1
    volume_velocity = volume / max(age_seconds, 1)
    buy_sell_momentum = (buy_count - sell_count) / max(buy_count + sell_count, 1)
    liquidity_depth = initial_liq / max(initial_mcap, 1) if initial_mcap > 0 else 0

    # Liquidity health indicators
    liq_concentration = 0.0  # Top LP holder % (needs external data)
    insider_holdings_pct = 0.0  # % held by early insiders

    # Volume spike detection: compare recent vs average
    volume_spike_ratio = 0.0
    if hasattr(launch_data, 'volume_history') and launch_data.volume_history:
        recent_vol = sum(launch_data.volume_history[-3:]) / max(len(launch_data.volume_history[-3:]), 1)
        avg_vol = sum(launch_data.volume_history) / max(len(launch_data.volume_history), 1)
        volume_spike_ratio = recent_vol / max(avg_vol, 1)
    elif isinstance(launch_data, dict) and "volume_history" in launch_data:
        vh = launch_data.get("volume_history", [])
        if vh:
            recent_vol = sum(vh[-3:]) / max(len(vh[-3:]), 1)
            avg_vol = sum(vh) / max(len(vh), 1)
            volume_spike_ratio = recent_vol / max(avg_vol, 1)

    # Buy spike: recent buys vs average
    buy_spike_ratio = 0.0
    if hasattr(launch_data, 'buy_history') and launch_data.buy_history:
        recent_buys = sum(launch_data.buy_history[-3:]) / max(len(launch_data.buy_history[-3:]), 1)
        avg_buys = sum(launch_data.buy_history) / max(len(launch_data.buy_history), 1)
        buy_spike_ratio = recent_buys / max(avg_buys, 1)
    elif isinstance(launch_data, dict) and "buy_history" in launch_data:
        bh = launch_data.get("buy_history", [])
        if bh:
            recent_buys = sum(bh[-3:]) / max(len(bh[-3:]), 1)
            avg_buys = sum(bh) / max(len(bh), 1)
            buy_spike_ratio = recent_buys / max(avg_buys, 1)

    return {
        # Core trading features
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
        "lp_providers_count": getattr(launch_data, 'lp_providers_count', 0) if hasattr(launch_data, 'lp_providers_count') else launch_data.get("lp_providers_count", 0) if isinstance(launch_data, dict) else 0,
        "deployer_has_lp": getattr(launch_data, 'deployer_has_lp', False) if hasattr(launch_data, 'deployer_has_lp') else launch_data.get("deployer_has_lp", False) if isinstance(launch_data, dict) else False,
        # Derived features
        "volume_velocity": round(volume_velocity, 4),
        "buy_sell_momentum": round(buy_sell_momentum, 4),
        "liquidity_depth": round(liquidity_depth, 4),
        # Volume spike detection (NEW)
        "volume_spike_ratio": round(volume_spike_ratio, 4),
        "buy_spike_ratio": round(buy_spike_ratio, 4),
        # Social signal
        "social_score": getattr(launch_data, 'social_score', 0) if hasattr(launch_data, 'social_score') else launch_data.get("social_score", 0) if isinstance(launch_data, dict) else 0,
        # Time features
        "launch_weekday": launch_weekday,
        "launch_month": launch_month,
        "is_weekend": is_weekend,
        "launch_session": launch_session,
        # Insider features
        "insider_count": len(insider_wallets),
    }


def _pattern_similarity(features: dict, pattern: dict) -> float:
    """Calculate weighted similarity between features and a known pattern. 0.0 to 1.0.
    Uses FEATURE_WEIGHTS for importance weighting.
    Handles both flat patterns and nested patterns (features inside 'features' key).
    Applies recency decay: newer patterns get bonus, old patterns get penalty."""
    weighted_score = 0.0
    total_weight = 0.0

    # Handle nested format: pattern may have features in pattern["features"]
    pat = pattern
    if "features" in pattern and "buy_sell_ratio" not in pattern:
        pat = pattern["features"]

    def _compare(feat_key, pat_key):
        nonlocal weighted_score, total_weight
        weight = FEATURE_WEIGHTS.get(feat_key, 1.0)
        f_val = features.get(feat_key, 0)
        p_val = pat.get(pat_key, 0)
        if p_val == 0 and f_val == 0:
            return
        if p_val == 0:
            return
        ratio = min(f_val, p_val) / max(f_val, p_val) if max(f_val, p_val) > 0 else 0
        weighted_score += ratio * weight
        total_weight += weight

    _compare("buy_sell_ratio", "buy_sell_ratio")
    _compare("holders", "holders")
    _compare("unique_wallets", "unique_wallets")
    _compare("snipers_30s", "snipers_30s")
    _compare("lp_locked", "lp_locked")
    _compare("volume_velocity", "volume_velocity")
    _compare("buy_sell_momentum", "buy_sell_momentum")
    _compare("liquidity_depth", "liquidity_depth")
    _compare("social_score", "social_score")
    _compare("insider_count", "insider_count")
    _compare("lp_providers_count", "lp_providers_count")
    _compare("volume_spike_ratio", "volume_spike_ratio")
    _compare("buy_spike_ratio", "buy_spike_ratio")

    # Launch session: categorical (asian/european/us)
    f_session = features.get("launch_session", "")
    p_session = pat.get("launch_session", "")
    if f_session and p_session:
        weight = FEATURE_WEIGHTS.get("launch_session", 1.0)
        if f_session == p_session:
            weighted_score += 1.0 * weight
        else:
            weighted_score += 0.3 * weight  # partial match
        total_weight += weight

    # Launch weekday: 0-6, closer = better
    f_weekday = features.get("launch_weekday", -1)
    p_weekday = pat.get("launch_weekday", -1)
    if f_weekday >= 0 and p_weekday >= 0:
        weight = FEATURE_WEIGHTS.get("launch_weekday", 0.8)
        if f_weekday == p_weekday:
            weighted_score += 1.0 * weight
        elif abs(f_weekday - p_weekday) <= 1:
            weighted_score += 0.6 * weight
        elif abs(f_weekday - p_weekday) <= 2:
            weighted_score += 0.3 * weight
        total_weight += weight

    if features.get("liq_pct", 0) > 0 and pat.get("liq_pct", 0) > 0:
        ratio = min(features["liq_pct"], pat["liq_pct"]) / max(features["liq_pct"], pat["liq_pct"])
        weight = FEATURE_WEIGHTS.get("liq_pct", 1.0)
        weighted_score += ratio * weight
        total_weight += weight

    if features.get("launch_hour", 0) == pat.get("launch_hour", 0):
        weight = FEATURE_WEIGHTS.get("launch_hour", 0.5)
        weighted_score += 1.0 * weight
        total_weight += weight
    elif abs(features.get("launch_hour", 0) - pat.get("launch_hour", 0)) <= 2:
        weight = FEATURE_WEIGHTS.get("launch_hour", 0.5)
        weighted_score += 0.5 * weight
        total_weight += weight

    base_score = weighted_score / total_weight if total_weight > 0 else 0.0

    # Recency decay: newer patterns get bonus, old patterns get penalty
    # learned_at or timestamp in pattern
    learned_str = pattern.get("learned_at") or pattern.get("timestamp") or ""
    if learned_str:
        try:
            learned_dt = datetime.fromisoformat(learned_str.replace("Z", "+00:00"))
            days_old = (datetime.now(timezone.utc) - learned_dt).days
            # Bonus: <3 days = +10%, <7 days = +5%, >14 days = -10%, >30 days = -20%
            if days_old <= 3:
                recency_bonus = 0.10
            elif days_old <= 7:
                recency_bonus = 0.05
            elif days_old <= 14:
                recency_bonus = 0.0
            elif days_old <= 30:
                recency_bonus = -0.10
            else:
                recency_bonus = -0.20
            base_score = max(0.0, min(1.0, base_score + recency_bonus))
        except Exception:
            pass

    return base_score


FEATURE_WEIGHTS = {
    "unique_wallets": 3.0,      # BEST separator (58%)
    "liquidity": 2.5,           # Good separator
    "volume_velocity": 2.0,     # Volume per second
    "buy_sell_momentum": 2.0,   # Net momentum
    "volume_spike_ratio": 2.5,  # Volume spike detection (NEW)
    "buy_spike_ratio": 2.0,     # Buy spike detection (NEW)
    "buy_sell_ratio": 1.0,      # POOR separator (22%)
    "holders": 1.0,             # POOR separator
    "lp_locked": 1.5,           # Moderate
    "snipers_30s": 1.5,         # Moderate
    "liq_pct": 1.5,             # Moderate
    "launch_hour": 0.5,         # Weak
    "social_score": 1.0,        # Social signal strength
    # Time-based features
    "launch_weekday": 0.8,      # Day-of-week matters
    "launch_session": 1.0,      # Asian/European/US session
    # Insider features
    "insider_count": 1.5,       # High insider count = risky
    # Liquidity features
    "lp_providers_count": 1.0,  # More providers = safer
}


def analyze_dump_quality() -> dict:
    """Analyze if dump patterns actually represent dump characteristics.
    Returns analysis with issues found."""
    data = load_data()
    dumps = data.get("dump_patterns", [])

    issues = []
    looks_like_pump = 0
    looks_like_dump = 0

    for d in dumps:
        feat = d.get("features", d) if isinstance(d, dict) else {}
        bsr = feat.get("buy_sell_ratio", 0)
        holders = feat.get("holders", 0)
        wallets = feat.get("unique_wallets", 0)
        liq = feat.get("initial_liq", 0) or feat.get("liquidity", 0)
        sym = d.get("symbol", "?")

        # Dump tokens should NOT have high BSR AND high holders
        # That indicates pre-pump hype, not dump characteristics
        if bsr > 2.0 and holders > 30 and wallets > 50:
            looks_like_pump += 1
            issues.append(f"{sym}: BSR={bsr:.1f} holders={holders} wallets={wallets} (looks like pump)")
        else:
            looks_like_dump += 1

    return {
        "total_dumps": len(dumps),
        "looks_like_pump": looks_like_pump,
        "looks_like_dump": looks_like_dump,
        "issues": issues[:50],  # Top 50 issues
        "quality_pct": round(looks_like_dump / max(len(dumps), 1) * 100, 1),
    }


def reclassify_patterns() -> int:
    """Move dump patterns that look like pumps back to pump patterns.
    Returns number of reclassified patterns."""
    data = load_data()
    dumps = data.get("dump_patterns", [])
    pumps = data.get("pump_patterns", [])

    reclassified = 0
    for d in list(dumps):
        feat = d.get("features", d) if isinstance(d, dict) else {}
        bsr = feat.get("buy_sell_ratio", 0)
        holders = feat.get("holders", 0)
        wallets = feat.get("unique_wallets", 0)

        # If dump has pump characteristics, move to pump patterns
        if bsr > 2.0 and holders > 30 and wallets > 50:
            d["outcome"] = "RECLASSIFIED_PUMP"
            d["reclassified_at"] = datetime.now(timezone.utc).isoformat()
            pumps.append(d)
            dumps.remove(d)
            reclassified += 1

    data["pump_patterns"] = pumps[-MAX_PUMP_PATTERNS:]
    data["dump_patterns"] = dumps
    save_data(data)

    logger.info(f"🔄 Reclassified {reclassified} dump patterns → pump patterns")
    return reclassified


def analyze_feature_importance() -> dict:
    """Calculate which features best separate pump from dump patterns.
    Returns feature importance scores (0.0-1.0)."""
    data = load_data()
    pumps = data.get("pump_patterns", [])
    dumps = data.get("dump_patterns", [])

    importance = {}

    for feature_key in ["buy_sell_ratio", "holders", "unique_wallets",
                         "initial_liq", "liq_pct", "lp_locked", "snipers_30s"]:
        p_vals = []
        d_vals = []

        for p in pumps:
            feat = p.get("features", p) if isinstance(p, dict) else {}
            v = feat.get(feature_key, 0)
            if v > 0:
                p_vals.append(v)

        for d in dumps:
            feat = d.get("features", d) if isinstance(d, dict) else {}
            v = feat.get(feature_key, 0)
            if v > 0:
                d_vals.append(v)

        if p_vals and d_vals:
            p_avg = sum(p_vals) / len(p_vals)
            d_avg = sum(d_vals) / len(d_vals)
            p_median = sorted(p_vals)[len(p_vals) // 2]
            d_median = sorted(d_vals)[len(d_vals) // 2]

            # Separation: how different are pump vs dump averages
            separation = abs(p_avg - d_avg) / max(p_avg, d_avg)

            # Direction: which is higher for pumps (positive = pumps higher)
            direction = "higher" if p_avg > d_avg else "lower"

            importance[feature_key] = {
                "pump_avg": round(p_avg, 2),
                "dump_avg": round(d_avg, 2),
                "pump_median": round(p_median, 2),
                "dump_median": round(d_median, 2),
                "separation": round(separation * 100, 1),
                "direction": direction,
                "useful": separation > 0.20,  # >20% separation = useful
            }

    return importance


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
    patterns[-MAX_PUMP_PATTERNS:]
    data["pump_patterns"] = patterns

    data["model"]["total_pumps"] = len(data.get("pump_patterns", []))
    data["model"]["last_update"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"🟢 LEARNED PUMP: {launch.get('symbol')} mcap={launch.get('outcome_mcap', 0):.0f} "
                f"buy_sell={features.get('buy_sell_ratio')} holders={features.get('holders')} "
                f"liq%={features.get('liq_pct')} snipers={features.get('snipers_30s')}")

    # Record signal result for hourly stats and learning
    try:
        launch_mcp = features.get("launch_mcp", 0) or features.get("mcp", 0)
        outcome_mcap = launch.get("outcome_mcap", 0)
        ath_mult = outcome_mcap / launch_mcp if launch_mcp > 0 else 1.0
        signal_time = features.get("signal_time")
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time.replace("Z", "+00:00"))
        record_signal_result(
            launch.get("address", ""), launch.get("symbol", "?"),
            ath_mult, ath_mult, 0.0, signal_time, 0.0
        )
    except Exception:
        pass


def _learn_dump(launch: dict) -> None:
    """Learn from a dump outcome."""
    data = load_data()
    features = launch.get("features", {})
    features["outcome"] = "dump"
    features["outcome_mcap"] = launch.get("outcome_mcap", 0)
    features["symbol"] = launch.get("symbol", "?")

    patterns = data.setdefault("dump_patterns", [])
    patterns.append(features)
    patterns = patterns[-MAX_DUMP_PATTERNS:]
    data["dump_patterns"] = patterns

    # Remove any pump pattern for this address (it's a dump)
    addr = launch.get("address", "")
    pump_patterns = data.get("pump_patterns", [])
    before = len(pump_patterns)
    pump_patterns = [p for p in pump_patterns if p.get("address") != addr]
    if len(pump_patterns) < before:
        data["pump_patterns"] = pump_patterns
        logger.info(f"🗑️ পাম্প প্যাটার্ন বাদ: {launch.get('symbol')} (DUMP ফলাফল)")

    data["model"]["total_dumps"] = data["model"].get("total_dumps", 0) + 1
    data["model"]["last_update"] = datetime.now(timezone.utc).isoformat()

    # Record signal result for hourly stats and learning
    try:
        launch_mcp = features.get("launch_mcp", 0) or features.get("mcp", 0)
        outcome_mcap = launch.get("outcome_mcap", 0)
        ath_mult = outcome_mcap / launch_mcp if launch_mcp > 0 else 0.5
        signal_time = features.get("signal_time")
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time.replace("Z", "+00:00"))
        record_signal_result(
            launch.get("address", ""), launch.get("symbol", "?"),
            ath_mult, ath_mult, 0.0, signal_time, 0.0
        )
    except Exception:
        pass


def match_pump_patterns(features: dict, min_similarity: float = 0.50, channel_id: int = None) -> tuple[bool, float, str]:
    """Check if features match known pump patterns.
    Returns (match, score, reason).
    If channel_id is provided, applies channel reliability weight to score."""
    if min_similarity is None:
        criteria = get_signal_criteria()
        min_similarity = criteria.get("pattern_threshold", 0.60)

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

    # Apply channel weight if available
    effective_score = best_score
    channel_weight = 1.0
    if channel_id is not None:
        weights = get_channel_weights()
        channel_weight = weights.get(str(channel_id), 1.0)
        effective_score = best_score * channel_weight

    if effective_score >= min_similarity:
        reasons = []
        if features.get("buy_sell_ratio", 0) >= 1.5:
            reasons.append(f"buy_sell={features['buy_sell_ratio']:.1f}")
        if features.get("holders", 0) >= 3:
            reasons.append(f"holders={features['holders']}")
        if features.get("snipers_30s", 0) >= 2:
            reasons.append(f"snipers={features['snipers_30s']}")
        if features.get("liq_pct", 0) >= 5:
            reasons.append(f"liq%={features['liq_pct']:.1f}%")

        ch_tag = f" ch_weight={channel_weight:.1f}x" if channel_id else ""
        reason = f"Matched {best_match.get('symbol', '?')} ({best_score:.0%}→{effective_score:.0%}) " + " ".join(reasons) + ch_tag
        return True, effective_score, reason

    return False, effective_score, f"Best match {best_score:.0%} (eff {effective_score:.0%}) < {min_similarity:.0%}"


def match_dump_patterns(features: dict, min_similarity: float = 0.70) -> tuple[bool, float, str]:
    """Check if features match known dump patterns.
    Returns (is_dump, score, reason).
    Used to REJECT signals that look like historical dumps."""
    data = load_data()
    dump_patterns = data.get("dump_patterns", [])

    if not dump_patterns:
        return False, 0.0, "No dump patterns learned"

    best_score = 0.0
    best_match = None

    for pattern in dump_patterns:
        sim = _pattern_similarity(features, pattern)
        if sim > best_score:
            best_score = sim
            best_match = pattern

    if best_score >= min_similarity:
        match_sym = best_match.get("symbol", "?") if best_match else "?"
        reason = f"Matches dump pattern {match_sym} ({best_score:.0%})"
        return True, best_score, reason

    return False, best_score, f"Best dump match {best_score:.0%} < {min_similarity:.0%}"


def record_signal_result(address: str, symbol: str, ath_multiplier: float, current_multiplier: float = 0.0, signal_age: float = 0.0, signal_time=None, min_price: float = 0.0) -> None:
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
        "min_price_multiplier": min_price,
        "signal_age": signal_age,
        "signal_time": signal_time.isoformat() if isinstance(signal_time, datetime) else datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["model"]["signal_results"] = results[-500:]

    launches = data.get("launches_tracked", [])
    launch = next((l for l in launches if l.get("address") == address), None)

    if launch and launch.get("features"):
        features = launch["features"]
        features["ath_multiplier"] = ath_multiplier
        features["outcome"] = verdict
        features["signal_age"] = signal_age
        if verdict in ("PUMP", "STRONG_PUMP"):
            pump_patterns = data.setdefault("pump_patterns", [])
            # Deduplication: check if this address already exists
            existing = next((p for p in pump_patterns if p.get("address") == address), None)
            if not existing:
                pump_patterns.append({
                    "address": address,
                    "symbol": symbol,
                    "features": features,
                    "outcome": verdict,
                    "ath_multiplier": ath_multiplier,
                    "signal_age": signal_age,
                    "learned_at": datetime.now(timezone.utc).isoformat(),
                })
                data["model"]["total_pumps"] = data["model"].get("total_pumps", 0) + 1
                logger.info(f"📚 পাম্প প্যাটার্ন শেখা: {symbol} (ATH {ath_multiplier:.1f}x, age={signal_age:.0f}s)")
            else:
                # Update existing with better data
                existing["ath_multiplier"] = max(existing.get("ath_multiplier", 0), ath_multiplier)
                existing["learned_at"] = datetime.now(timezone.utc).isoformat()
        elif verdict == "DUMP":
            dump_patterns = data.setdefault("dump_patterns", [])
            existing = next((d for d in dump_patterns if d.get("address") == address), None)
            if not existing:
                dump_patterns.append({
                    "address": address,
                    "symbol": symbol,
                    "features": features,
                    "outcome": "DUMP",
                    "ath_multiplier": ath_multiplier,
                    "signal_age": signal_age,
                    "learned_at": datetime.now(timezone.utc).isoformat(),
                })
                data["model"]["total_dumps"] = len(data.get("dump_patterns", []))
                logger.info(f"📚 ডাম্প প্যাটার্ন শেখা: {symbol} (ATH {ath_multiplier:.1f}x, age={signal_age:.0f}s)")
            else:
                existing["ath_multiplier"] = ath_multiplier
                existing["learned_at"] = datetime.now(timezone.utc).isoformat()

            # Remove failed pump pattern — this token was a DUMP, so remove its pump pattern
            pump_patterns = data.get("pump_patterns", [])
            before = len(pump_patterns)
            pump_patterns = [p for p in pump_patterns if p.get("address") != address]
            if len(pump_patterns) < before:
                data["pump_patterns"] = pump_patterns
                logger.info(f"🗑️ পাম্প প্যাটার্ন বাদ: {symbol} (DUMP ফলাফল)")

        if len(data.get("pump_patterns", [])) > MAX_PUMP_PATTERNS:
            data["pump_patterns"] = data["pump_patterns"][-MAX_PUMP_PATTERNS:]
        if len(data.get("dump_patterns", [])) > MAX_DUMP_PATTERNS:
            data["dump_patterns"] = data["dump_patterns"][-MAX_DUMP_PATTERNS:]

    save_data(data)

    # Root cause analysis for DUMP signals
    if verdict == "DUMP" and launch and launch.get("features"):
        _analyze_and_fix_failure(address, symbol, launch["features"], data)

    # Success amplification for PUMP/STRONG_PUMP signals
    if verdict in ("PUMP", "STRONG_PUMP") and launch and launch.get("features"):
        _analyze_and_amplify_success(address, symbol, launch["features"], data, ath_multiplier)

    # Update hourly win rate stats
    _update_hourly_stats(data, signal_time, verdict)

    save_data(data)


def _analyze_and_fix_failure(address: str, symbol: str, features: dict, data: dict) -> None:
    """Analyze WHY a signal failed and auto-fix criteria."""
    criteria = data.get("model", {}).get("signal_criteria", {})
    failure_reasons = []

    bsr = features.get("buy_sell_ratio", 0)
    holders = features.get("holders", 0)
    wallets = features.get("unique_wallets", 0)
    liq = features.get("initial_liq", 0) or features.get("liquidity", 0)
    lp_locked = features.get("lp_locked", 0)
    snipers = features.get("snipers_30s", 0)
    deployer_has_lp = features.get("deployer_has_lp", False)
    liq_pct = features.get("liq_pct", 0)

    # Check each criterion — if signal was borderline, it's a likely cause
    min_bsr = criteria.get("min_bsr", 1.5)
    if bsr < min_bsr * 1.2:
        failure_reasons.append(f"low_bsr={bsr:.1f}")

    min_holders = criteria.get("min_holders", 5)
    if holders < min_holders * 1.3:
        failure_reasons.append(f"low_holders={holders}")

    min_wallets = criteria.get("min_wallets", 10)
    if wallets < min_wallets * 1.3:
        failure_reasons.append(f"low_wallets={wallets}")

    min_liq = criteria.get("min_liq", 1500)
    if liq < min_liq * 1.5:
        failure_reasons.append(f"low_liq=${int(liq)}")

    if deployer_has_lp:
        failure_reasons.append("deployer_has_lp")

    if snipers >= 5:
        failure_reasons.append(f"high_snipers={snipers}")

    if lp_locked < 50 and lp_locked > 0:
        failure_reasons.append(f"low_lp_locked={lp_locked}%")

    if not failure_reasons:
        failure_reasons.append("unknown")

    logger.warning(f"🔍 Failure analysis {symbol}: {', '.join(failure_reasons)}")

    # Auto-fix: raise thresholds for repeatedly failing criteria
    auto_fix = data.get("model", {}).setdefault("auto_fix_history", [])
    auto_fix.append({
        "address": address,
        "symbol": symbol,
        "reasons": failure_reasons,
        "features": {k: v for k, v in features.items() if isinstance(v, (int, float, str, bool))},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["model"]["auto_fix_history"] = auto_fix[-200:]

    # Count failure reasons across recent failures
    recent_fixes = auto_fix[-50:]
    reason_counts = {}
    for fix in recent_fixes:
        for r in fix.get("reasons", []):
            base = r.split("=")[0]
            reason_counts[base] = reason_counts.get(base, 0) + 1

    # If a reason appears in >30% of failures, tighten that criterion
    total_recent = len(recent_fixes)
    if total_recent >= 3:
        for reason, count in reason_counts.items():
            pct = count / total_recent
            if pct > 0.30:
                if reason == "low_bsr" and criteria.get("min_bsr", 1.5) < 2.0:
                    criteria["min_bsr"] = round(criteria["min_bsr"] + 0.1, 2)
                    logger.info(f"🔧 Auto-fix: min_bsr → {criteria['min_bsr']} ({pct:.0%} failures)")
                elif reason == "low_holders" and criteria.get("min_holders", 5) < 10:
                    criteria["min_holders"] = round(criteria["min_holders"] + 1, 1)
                    logger.info(f"🔧 Auto-fix: min_holders → {criteria['min_holders']} ({pct:.0%} failures)")
                elif reason == "low_wallets" and criteria.get("min_wallets", 10) < 30:
                    criteria["min_wallets"] = round(criteria["min_wallets"] + 2, 1)
                    logger.info(f"🔧 Auto-fix: min_wallets → {criteria['min_wallets']} ({pct:.0%} failures)")
                elif reason == "low_liq" and criteria.get("min_liq", 1500) < 5000:
                    criteria["min_liq"] = round(criteria["min_liq"] + 200, 0)
                    logger.info(f"🔧 Auto-fix: min_liq → ${int(criteria['min_liq'])} ({pct:.0%} failures)")
                elif reason == "deployer_has_lp":
                    logger.info(f"🔧 Auto-fix: deployer_has_lp detected — already blocked in signal flow")
                elif reason == "high_snipers":
                    logger.info(f"🔧 Auto-fix: high snipers — consider adding sniper filter")

    data["model"]["signal_criteria"] = criteria


def _analyze_and_amplify_success(address: str, symbol: str, features: dict, data: dict, ath_multiplier: float) -> None:
    """Analyze WHY a signal succeeded and amplify those patterns."""
    success_reasons = []

    bsr = features.get("buy_sell_ratio", 0)
    holders = features.get("holders", 0)
    wallets = features.get("unique_wallets", 0)
    liq = features.get("initial_liq", 0) or features.get("liquidity", 0)
    lp_locked = features.get("lp_locked", 0)
    snipers = features.get("snipers_30s", 0)
    liq_pct = features.get("liq_pct", 0)
    buy_count = features.get("buy_count", 0)
    volume_velocity = features.get("volume_velocity", 0)

    criteria = data.get("model", {}).get("signal_criteria", {})

    # Check which features were strong in this success
    if bsr >= criteria.get("min_bsr", 1.3) * 1.5:
        success_reasons.append(f"high_bsr={bsr:.1f}")
    if holders >= criteria.get("min_holders", 5) * 2:
        success_reasons.append(f"high_holders={holders}")
    if wallets >= criteria.get("min_wallets", 10) * 2:
        success_reasons.append(f"high_wallets={wallets}")
    if liq >= criteria.get("min_liq", 1500) * 2:
        success_reasons.append(f"high_liq=${int(liq)}")
    if lp_locked >= 90:
        success_reasons.append(f"high_lp_locked={lp_locked}%")
    if buy_count >= 30:
        success_reasons.append(f"high_buys={buy_count}")
    if volume_velocity >= 5:
        success_reasons.append(f"high_velocity={volume_velocity:.1f}")
    if snipers >= 3:
        success_reasons.append(f"high_snipers={snipers}")

    if not success_reasons:
        success_reasons.append("balanced")

    logger.info(f"🎯 Success analysis {symbol} (ATH {ath_multiplier:.1f}x): {', '.join(success_reasons)}")

    # Track success patterns for future reference
    success_history = data.get("model", {}).setdefault("success_history", [])
    success_history.append({
        "address": address,
        "symbol": symbol,
        "reasons": success_reasons,
        "ath_multiplier": ath_multiplier,
        "features": {k: v for k, v in features.items() if isinstance(v, (int, float, str, bool))},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["model"]["success_history"] = success_history[-200:]

    # If ATH > 5x, this is a very strong success — relax thresholds slightly
    if ath_multiplier >= 5.0:
        if criteria.get("min_holders", 5) > 3:
            criteria["min_holders"] = round(criteria["min_holders"] - 0.5, 1)
            logger.info(f"🔧 Success relax: min_holders -> {criteria['min_holders']} (5x+ success)")
        if criteria.get("min_wallets", 10) > 5:
            criteria["min_wallets"] = round(criteria["min_wallets"] - 1, 1)
            logger.info(f"🔧 Success relax: min_wallets -> {criteria['min_wallets']} (5x+ success)")

    data["model"]["signal_criteria"] = criteria


def _update_hourly_stats(data: dict, signal_time, verdict: str, pnl: float = 0.0) -> None:
    """Track win rate AND pnl per UTC hour for dynamic time filter."""
    if isinstance(signal_time, datetime):
        hour = signal_time.hour
    elif isinstance(signal_time, str):
        try:
            hour = datetime.fromisoformat(signal_time.replace("Z", "+00:00")).hour
        except Exception:
            hour = datetime.now(timezone.utc).hour
    else:
        hour = datetime.now(timezone.utc).hour

    hour_str = str(hour)  # JSON keys are strings

    hourly = data.get("model", {}).setdefault("hourly_stats", {})

    if hour_str not in hourly:
        hourly[hour_str] = {"wins": 0, "total": 0, "total_pnl": 0.0}

    hourly[hour_str]["total"] = hourly[hour_str].get("total", 0) + 1
    hourly[hour_str]["total_pnl"] = hourly[hour_str].get("total_pnl", 0.0) + pnl
    if verdict in ("PUMP", "STRONG_PUMP"):
        hourly[hour_str]["wins"] = hourly[hour_str].get("wins", 0) + 1


def get_bad_hours(min_signals: int = 5, max_win_rate: float = 0.15) -> set:
    """Return set of UTC hours with historically low win rate.
    Requires at least min_signals samples per hour to be considered."""
    data = load_data()
    hourly = data.get("model", {}).get("hourly_stats", {})
    bad_hours = set()

    for hour_str, stats in hourly.items():
        hour = int(hour_str)
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        if total >= min_signals:
            win_rate = wins / total
            if win_rate < max_win_rate:
                bad_hours.add(hour)
                logger.info(f"⏰ Bad hour detected: {hour}:00 UTC — {wins}/{total} = {win_rate:.0%} win rate")

    return bad_hours


def get_good_hours(min_signals: int = 5, min_pump_rate: float = 0.80) -> set:
    """Return set of UTC hours with historically HIGH pump rate (≥80%).
    Only includes hours with at least min_signals samples.
    If no good hours found, returns empty set (caller should allow all hours)."""
    data = load_data()
    hourly = data.get("model", {}).get("hourly_stats", {})
    good_hours = set()

    for hour_str, stats in hourly.items():
        hour = int(hour_str)
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        if total >= min_signals:
            pump_rate = wins / total
            if pump_rate >= min_pump_rate:
                good_hours.add(hour)
                logger.info(f"⏰ Good hour: {hour}:00 UTC — {wins}/{total} = {pump_rate:.0%} pump rate")

    return good_hours


def remove_low_quality_patterns(min_pump_rate: float = 0.80, min_signals: int = 5) -> int:
    """Remove pump patterns from hours with <80% pump rate.
    Only removes when we have enough data (min_signals per hour).
    Returns number of patterns removed."""
    data = load_data()
    pumps = data.get("pump_patterns", [])
    hourly = data.get("model", {}).get("hourly_stats", {})

    if not hourly or len(pumps) == 0:
        return 0

    # Group pump patterns by launch_hour
    hour_groups = {}
    for p in pumps:
        feat = p.get("features", p) if isinstance(p, dict) else {}
        hour = feat.get("launch_hour", 0)
        if hour not in hour_groups:
            hour_groups[hour] = []
        hour_groups[hour].append(p)

    # Check pump rate per hour group and remove low-quality
    removed = 0
    for hour, patterns in hour_groups.items():
        stats = hourly.get(str(hour), {})
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)

        if total >= min_signals:
            pump_rate = wins / total
            if pump_rate < min_pump_rate:
                for p in patterns:
                    if p in pumps:
                        pumps.remove(p)
                        removed += 1
                logger.info(f"🗑️ Removed {len(patterns)} patterns from hour {hour}:00 "
                           f"(pump_rate={pump_rate:.0%} < {min_pump_rate:.0%})")

    if removed > 0:
        data["pump_patterns"] = pumps
        save_data(data)
        logger.info(f"🗑️ Total removed: {removed} low-quality patterns")

    return removed


def get_hourly_stats_report() -> str:
    """Generate hourly stats report for debugging."""
    data = load_data()
    hourly = data.get("model", {}).get("hourly_stats", {})
    if not hourly:
        return "No hourly data yet"

    lines = ["📊 Hourly Win Rate:"]
    for hour in sorted(hourly.keys(), key=lambda x: int(x)):
        stats = hourly[hour]
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        wr = (wins / total * 100) if total > 0 else 0
        bar = "🟢" if wr > 25 else ("🟡" if wr > 15 else "🔴")
        lines.append(f"  {bar} {int(hour):02d}:00 — {wins}/{total} = {wr:.0f}%")
    return "\n".join(lines)


def get_time_pattern_analytics() -> dict:
    """Comprehensive time-based pattern analysis from pump/dump patterns."""
    data = load_data()
    pumps = data.get("pump_patterns", [])
    dumps = data.get("dump_patterns", [])

    # Day-of-week analysis (0=Monday, 6=Sunday)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekly_stats = {i: {"pumps": 0, "dumps": 0, "total_ath": 0.0} for i in range(7)}

    for p in pumps:
        feat = p.get("features", p) if isinstance(p, dict) else {}
        day = feat.get("launch_weekday", -1)
        if 0 <= day <= 6:
            weekly_stats[day]["pumps"] += 1
            weekly_stats[day]["total_ath"] += p.get("ath_multiplier", 0) or 0

    for d in dumps:
        feat = d.get("features", d) if isinstance(d, dict) else {}
        day = feat.get("launch_weekday", -1)
        if 0 <= day <= 6:
            weekly_stats[day]["dumps"] += 1

    # Monthly analysis
    monthly_stats = {m: {"pumps": 0, "dumps": 0, "total_ath": 0.0} for m in range(1, 13)}
    for p in pumps:
        feat = p.get("features", p) if isinstance(p, dict) else {}
        month = feat.get("launch_month", 0)
        if 1 <= month <= 12:
            monthly_stats[month]["pumps"] += 1
            monthly_stats[month]["total_ath"] += p.get("ath_multiplier", 0) or 0
    for d in dumps:
        feat = d.get("features", d) if isinstance(d, dict) else {}
        month = feat.get("launch_month", 0)
        if 1 <= month <= 12:
            monthly_stats[month]["dumps"] += 1

    # Session analysis (asian/european/us)
    session_stats = {
        "asian": {"pumps": 0, "dumps": 0, "total_ath": 0.0},
        "european": {"pumps": 0, "dumps": 0, "total_ath": 0.0},
        "us": {"pumps": 0, "dumps": 0, "total_ath": 0.0},
    }
    for p in pumps:
        feat = p.get("features", p) if isinstance(p, dict) else {}
        session = feat.get("launch_session", "")
        if session in session_stats:
            session_stats[session]["pumps"] += 1
            session_stats[session]["total_ath"] += p.get("ath_multiplier", 0) or 0
    for d in dumps:
        feat = d.get("features", d) if isinstance(d, dict) else {}
        session = feat.get("launch_session", "")
        if session in session_stats:
            session_stats[session]["dumps"] += 1

    # Weekend vs weekday
    weekend_pumps = sum(1 for p in pumps if (p.get("features", p) if isinstance(p, dict) else {}).get("is_weekend", False))
    weekend_dumps = sum(1 for d in dumps if (d.get("features", d) if isinstance(d, dict) else {}).get("is_weekend", False))
    weekday_pumps = len(pumps) - weekend_pumps
    weekday_dumps = len(dumps) - weekend_dumps

    # Insider analysis
    insider_pumps = sum(1 for p in pumps if (p.get("features", p) if isinstance(p, dict) else {}).get("insider_count", 0) > 0)
    insider_dumps = sum(1 for d in dumps if (d.get("features", d) if isinstance(d, dict) else {}).get("insider_count", 0) > 0)

    return {
        "weekly": {day_names[k]: {
            "pumps": v["pumps"],
            "dumps": v["dumps"],
            "pump_rate": round(v["pumps"] / max(v["pumps"] + v["dumps"], 1) * 100, 1),
            "avg_ath": round(v["total_ath"] / max(v["pumps"], 1), 2)
        } for k, v in weekly_stats.items()},
        "monthly": {m: {
            "pumps": v["pumps"],
            "dumps": v["dumps"],
            "pump_rate": round(v["pumps"] / max(v["pumps"] + v["dumps"], 1) * 100, 1),
            "avg_ath": round(v["total_ath"] / max(v["pumps"], 1), 2)
        } for m, v in monthly_stats.items()},
        "sessions": {s: {
            "pumps": v["pumps"],
            "dumps": v["dumps"],
            "pump_rate": round(v["pumps"] / max(v["pumps"] + v["dumps"], 1) * 100, 1),
            "avg_ath": round(v["total_ath"] / max(v["pumps"], 1), 2)
        } for s, v in session_stats.items()},
        "weekend": {
            "pumps": weekend_pumps, "dumps": weekend_dumps,
            "pump_rate": round(weekend_pumps / max(weekend_pumps + weekend_dumps, 1) * 100, 1)
        },
        "weekday": {
            "pumps": weekday_pumps, "dumps": weekday_dumps,
            "pump_rate": round(weekday_pumps / max(weekday_pumps + weekday_dumps, 1) * 100, 1)
        },
        "insiders": {
            "insider_pumps": insider_pumps,
            "insider_dumps": insider_dumps,
            "insider_pump_rate": round(insider_pumps / max(insider_pumps + insider_dumps, 1) * 100, 1)
        }
    }


def get_launch_pattern_analytics() -> dict:
    """Analyze what launch patterns lead to pumps vs dumps."""
    data = load_data()
    pumps = data.get("pump_patterns", [])
    dumps = data.get("dump_patterns", [])

    def _get_features(patterns):
        features = []
        for p in patterns:
            feat = p.get("features", p) if isinstance(p, dict) else {}
            features.append(feat)
        return features

    pump_feats = _get_features(pumps)
    dump_feats = _get_features(dumps)

    def _avg(lst, key):
        vals = [f.get(key, 0) for f in lst if f.get(key, 0) > 0]
        return round(sum(vals) / max(len(vals), 1), 2)

    def _median(lst, key):
        vals = sorted([f.get(key, 0) for f in lst if f.get(key, 0) > 0])
        if not vals:
            return 0
        mid = len(vals) // 2
        return round(vals[mid], 2)

    def _pct_above(lst, key, threshold):
        above = sum(1 for f in lst if f.get(key, 0) >= threshold)
        return round(above / max(len(lst), 1) * 100, 1)

    return {
        "pump_count": len(pumps),
        "dump_count": len(dumps),
        "pump_patterns": {
            "avg_buy_count": _avg(pump_feats, "buy_count"),
            "avg_wallets": _avg(pump_feats, "unique_wallets"),
            "avg_holders": _avg(pump_feats, "holders"),
            "avg_liq": _avg(pump_feats, "initial_liq"),
            "avg_mcap": _avg(pump_feats, "initial_mcap"),
            "avg_bsr": _avg(pump_feats, "buy_sell_ratio"),
            "avg_buys_30s": _avg(pump_feats, "snipers_30s"),
            "avg_liq_pct": _avg(pump_feats, "liq_pct"),
            "avg_lp_locked": _avg(pump_feats, "lp_locked"),
            "avg_volume_velocity": _avg(pump_feats, "volume_velocity"),
            "avg_social_score": _avg(pump_feats, "social_score"),
            "pct_with_lp_lock": _pct_above(pump_feats, "lp_locked", 80),
            "pct_high_buys": _pct_above(pump_feats, "buy_count", 15),
            "pct_high_wallets": _pct_above(pump_feats, "unique_wallets", 20),
        },
        "dump_patterns": {
            "avg_buy_count": _avg(dump_feats, "buy_count"),
            "avg_wallets": _avg(dump_feats, "unique_wallets"),
            "avg_holders": _avg(dump_feats, "holders"),
            "avg_liq": _avg(dump_feats, "initial_liq"),
            "avg_mcap": _avg(dump_feats, "initial_mcap"),
            "avg_bsr": _avg(dump_feats, "buy_sell_ratio"),
            "avg_buys_30s": _avg(dump_feats, "snipers_30s"),
            "avg_liq_pct": _avg(dump_feats, "liq_pct"),
            "avg_lp_locked": _avg(dump_feats, "lp_locked"),
            "pct_with_lp_lock": _pct_above(dump_feats, "lp_locked", 80),
        },
        "success_factors": {
            "wallets_20plus": _pct_above(pump_feats, "unique_wallets", 20),
            "liq_3k_plus": _pct_above(pump_feats, "initial_liq", 3000),
            "lp_locked_80plus": _pct_above(pump_feats, "lp_locked", 80),
            "holders_10plus": _pct_above(pump_feats, "holders", 10),
            "bsr_1_5plus": _pct_above(pump_feats, "buy_sell_ratio", 1.5),
            "social_0_5plus": _pct_above(pump_feats, "social_score", 0.5),
        }
    }


def get_comprehensive_analytics() -> str:
    """Generate comprehensive analytics report for /analytics command."""
    data = load_data()
    model = data.get("model", {})
    results = model.get("signal_results", [])

    # Basic stats
    total = len(results)
    pumps = sum(1 for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP"))
    strong = sum(1 for r in results if r.get("verdict") == "STRONG_PUMP")
    wins = pumps
    win_rate = round(wins / max(total, 1) * 100, 1)

    # Time patterns
    time_data = get_time_pattern_analytics()
    launch_data = get_launch_pattern_analytics()

    # Find best/worst day
    best_day = max(time_data["weekly"].items(), key=lambda x: x[1]["pump_rate"])
    worst_day = min(time_data["weekly"].items(), key=lambda x: x[1]["pump_rate"])

    # Find best session
    best_session = max(time_data["sessions"].items(), key=lambda x: x[1]["pump_rate"])

    # Build report
    lines = []
    lines.append("📊 COMPREHENSIVE ANALYTICS")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    # Performance
    lines.append(f"\n📈 PERFORMANCE:")
    lines.append(f"  Total Signals: {total}")
    lines.append(f"  Win Rate: {win_rate}% ({wins}/{total})")
    lines.append(f"  Strong Pumps: {strong}")

    # Time patterns
    lines.append(f"\n⏰ TIME PATTERNS:")
    lines.append(f"  Best Day: {best_day[0]} ({best_day[1]['pump_rate']}% pump, avg ATH {best_day[1]['avg_ath']}x)")
    lines.append(f"  Worst Day: {worst_day[0]} ({worst_day[1]['pump_rate']}% pump)")
    lines.append(f"  Best Session: {best_session[0]} ({best_session[1]['pump_rate']}% pump)")

    # Weekend vs weekday
    wd = time_data["weekend"]
    we = time_data["weekday"]
    lines.append(f"  Weekday: {we['pump_rate']}% pump ({we['pumps']}/{we['pumps'] + we['dumps']})")
    lines.append(f"  Weekend: {wd['pump_rate']}% pump ({wd['pumps']}/{wd['pumps'] + wd['dumps']})")

    # Insider analysis
    ins = time_data["insiders"]
    lines.append(f"\n🔍 INSIDER ANALYSIS:")
    lines.append(f"  With Insiders: {ins['insider_pump_rate']}% pump ({ins['insider_pumps']}/{ins['insider_pumps'] + ins['insider_dumps']})")

    # Launch patterns
    pp = launch_data["pump_patterns"]
    dp = launch_data["dump_patterns"]
    lines.append(f"\n🎯 LAUNCH PATTERNS:")
    lines.append(f"  Pump avg wallets: {pp['avg_wallets']}")
    lines.append(f"  Dump avg wallets: {dp['avg_wallets']}")
    lines.append(f"  Pump avg liq: ${pp['avg_liq']:.0f}")
    lines.append(f"  Dump avg liq: ${dp['avg_liq']:.0f}")
    lines.append(f"  Pump avg holders: {pp['avg_holders']}")
    lines.append(f"  Dump avg holders: {dp['avg_holders']}")

    # Success factors
    sf = launch_data["success_factors"]
    lines.append(f"\n✅ SUCCESS FACTORS:")
    lines.append(f"  Wallets 20+: {sf['wallets_20plus']}% of pumps")
    lines.append(f"  Liq $3k+: {sf['liq_3k_plus']}% of pumps")
    lines.append(f"  LP Locked 80%+: {sf['lp_locked_80plus']}% of pumps")
    lines.append(f"  Holders 10+: {sf['holders_10plus']}% of pumps")
    lines.append(f"  BSR 1.5+: {sf['bsr_1_5plus']}% of pumps")

    # Criteria
    criteria = model.get("signal_criteria", {})
    lines.append(f"\n⚙️ CURRENT CRITERIA:")
    lines.append(f"  min_wallets: {criteria.get('min_wallets', 10)}")
    lines.append(f"  min_holders: {criteria.get('min_holders', 5)}")
    lines.append(f"  min_liq: ${criteria.get('min_liq', 1500):.0f}")
    lines.append(f"  min_lp_locked: {criteria.get('min_lp_locked', 80)}%")
    lines.append(f"  min_bsr: {criteria.get('min_bsr', 1.3)}")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 {len(data.get('pump_patterns', []))} pump / {len(data.get('dump_patterns', []))} dump patterns")

    return "\n".join(lines)


def get_stats() -> dict:
    """Get current learning stats. Respects fresh_start marker."""
    data = load_data()
    model = data.get("model", {})
    results = model.get("signal_results", [])
    fresh_start = data.get("fresh_start", "")
    pumps = data.get("pump_patterns", [])
    dumps = data.get("dump_patterns", [])

    now = datetime.now(timezone.utc).timestamp()
    yesterday = now - 86400
    recent = []
    for r in results:
        if r.get("source") == "collector_sync":
            continue
        ts = r.get("timestamp") or r.get("detected_at", "")
        if not ts or ts == "N/A":
            continue
        try:
            if fresh_start and ts < fresh_start:
                continue
            if ts >= datetime.fromtimestamp(yesterday, tz=timezone.utc).isoformat():
                recent.append(r)
        except Exception:
            continue

    if not recent:
        recent = [r for r in results if r.get("source") != "collector_sync" and r.get("current_multiplier", 0) > 0]
        if fresh_start:
            recent = [r for r in recent if (r.get("timestamp") or "") >= fresh_start]
        # Filter old re-synced winners (>10x ATH = old token)
        recent = [r for r in recent if r.get("ath_multiplier", 0) <= 10]
        recent = recent[-50:]

    # For TP/SL: only use fresh signals, not old collector_sync data
    all_valid = [r for r in results if r.get("current_multiplier", 0) > 0 and r.get("ath_multiplier", 0) > 0 and r.get("ath_multiplier", 0) <= 10]
    if fresh_start:
        all_valid = [r for r in all_valid if (r.get("timestamp") or "") >= fresh_start]

    if not recent and not all_valid:
        return {
            "total": 0, "wins": 0, "losses": 0, "pending": 0,
            "win_rate": 0, "avg_ath": 0, "best": None, "worst": None,
            "optimal_tp": 50, "optimal_sl": -25, "expected_pnl": 0,
            "signals": [],
        }

    # Stats from recent real-time signals only
    wins = [r for r in recent if r.get("verdict") in ("PUMP", "STRONG_PUMP", "MEGA_PUMP")]
    losses = [r for r in recent if r.get("verdict") == "DUMP"]

    aths = [r.get("ath_multiplier", 1) for r in recent if r.get("ath_multiplier", 0) > 0]
    avg_ath = sum(aths) / len(aths) if aths else 0

    best = max(recent, key=lambda r: r.get("ath_multiplier", 0)) if recent else None
    worst = min(recent, key=lambda r: r.get("ath_multiplier", 0)) if recent else None

    # TP/SL from fresh data only
    optimal = calculate_optimal_tp_sl(all_valid if all_valid else recent)

    signals = []
    for r in sorted(recent, key=lambda x: x.get("timestamp", ""), reverse=True):
        ath = r.get("ath_multiplier", 1)
        if ath >= 5:
            emoji = "🔥"
        elif ath >= 2:
            emoji = "✅"
        elif ath >= 1:
            emoji = "😐"
        else:
            emoji = "❌"
        signals.append({
            "symbol": r.get("symbol", "?"),
            "ath": ath,
            "emoji": emoji,
            "verdict": r.get("verdict", "?"),
        })

    scenarios = simulate_tp_scenarios(all_valid if all_valid else recent)
    trailing = simulate_trailing_stop(all_valid if all_valid else recent)
    time_analysis = get_time_analysis(all_valid if all_valid else recent)
    risk_adjusted = calculate_risk_adjusted_tp_sl(all_valid if all_valid else recent)

    return {
        "total": len(recent),
        "wins": len(wins),
        "losses": len(losses),
        "pending": len(recent) - len(wins) - len(losses),
        "win_rate": round(len(wins) / max(len(recent), 1) * 100, 1),
        "avg_ath": round(avg_ath, 2),
        "best": best,
        "worst": worst,
        "optimal_tp": optimal["optimal_tp"],
        "optimal_sl": optimal["optimal_sl"],
        "expected_pnl": optimal["expected_pnl"],
        "optimal_win_rate": optimal.get("win_rate", 0),
        "tp_hits": optimal.get("tp_hits", 0),
        "sl_hits": optimal.get("sl_hits", 0),
        "holds": optimal.get("holds", 0),
        "tp_scenarios": scenarios,
        "signals": signals,
        "trailing": trailing,
        "time_analysis": time_analysis,
        "risk_adjusted": risk_adjusted,
        "total_all_data": len(all_valid),
        "total_pumps": model.get("total_pumps", len(pumps)),
        "total_dumps": model.get("total_dumps", len(dumps)),
    }


def calculate_signal_review() -> dict:
    """Review all signals since fresh start: actual pump, optimal SL, win/loss status.
    Called every 6 hours by the signal_review_loop."""
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    fresh_start = data.get("fresh_start", "")

    if not fresh_start:
        return {"signals": [], "summary": "No fresh start marker found"}

    # Only signals after fresh start
    signals_after_fresh = []
    for r in results:
        ts = r.get("timestamp") or r.get("detected_at", "")
        if not ts:
            continue
        try:
            if ts >= fresh_start:
                signals_after_fresh.append(r)
        except Exception:
            continue

    if not signals_after_fresh:
        return {"signals": [], "summary": "No signals since fresh start"}

    reviewed = []
    for r in signals_after_fresh:
        ath = r.get("ath_multiplier", 1)
        current = r.get("current_multiplier", 0)
        min_price = r.get("min_price_multiplier", 0)
        entry_mcap = r.get("entry_mcap", 0)
        symbol = r.get("symbol", "?")
        address = r.get("address", "")
        verdict = r.get("verdict", "DUMP")
        ts = r.get("timestamp", "")

        # Calculate actual performance
        actual_pump_pct = round((ath - 1) * 100, 1) if ath > 1 else round((ath - 1) * 100, 1)
        current_pump_pct = round((current - 1) * 100, 1) if current > 0 else 0

        # Calculate optimal SL: what % drop would NOT have hit SL
        # If min_price > 0, the optimal SL is just below min_price
        if min_price > 0 and min_price < 1:
            optimal_sl_pct = round((min_price - 1) * 100, 1)
        else:
            optimal_sl_pct = -50  # conservative default

        # Determine if signal was profitable
        is_win = verdict in ("PUMP", "STRONG_PUMP", "MEGA_PUMP")
        status_emoji = "🔥" if ath >= 5 else ("✅" if ath >= 2 else ("😐" if ath >= 1 else "❌"))

        reviewed.append({
            "symbol": symbol,
            "address": address[:8] + "...",
            "ath_multiplier": round(ath, 2),
            "current_multiplier": round(current, 2) if current > 0 else None,
            "actual_pump_pct": actual_pump_pct,
            "current_pump_pct": current_pump_pct,
            "optimal_sl_pct": optimal_sl_pct,
            "verdict": verdict,
            "is_win": is_win,
            "status_emoji": status_emoji,
            "entry_mcap": entry_mcap,
            "timestamp": ts,
        })

    # Sort by timestamp (newest first)
    reviewed.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    # Summary
    total = len(reviewed)
    wins = sum(1 for r in reviewed if r["is_win"])
    losses = total - wins
    avg_ath = sum(r["ath_multiplier"] for r in reviewed) / max(total, 1)
    best = max(reviewed, key=lambda x: x["ath_multiplier"]) if reviewed else None
    worst = min(reviewed, key=lambda x: x["ath_multiplier"]) if reviewed else None

    return {
        "signals": reviewed,
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(total, 1) * 100, 1),
        "avg_ath": round(avg_ath, 2),
        "best": best,
        "worst": worst,
        "fresh_start": fresh_start,
    }


def _exit_pnl(ath, current, tp_mult, sl_mult, min_price=0.0):
    if current <= 0:
        current = ath if ath > 0 else 1.0
    if min_price <= 0:
        min_price = current if current < ath else ath
    if min_price > 0 and min_price <= sl_mult:
        return ((sl_mult - 1) * 100, "sl")
    elif ath >= tp_mult:
        return ((tp_mult - 1) * 100, "tp")
    else:
        return ((current - 1) * 100, "hold")


def calculate_optimal_tp_sl(results):
    if not results:
        return {"optimal_tp": 50, "optimal_sl": -25, "expected_pnl": 0,
                "win_rate": 0, "tp_hits": 0, "sl_hits": 0, "holds": 0}
    best_score = -999
    best_pnl = -999
    best_tp = 50
    best_sl = -25
    for tp_pct in range(5, 201, 5):
        for sl_pct in range(-50, -5, 5):
            tp_mult = 1 + tp_pct / 100
            sl_mult = 1 + sl_pct / 100
            total_pnl = 0
            tp_hits = 0
            sl_hits = 0
            holds = 0
            for r in results:
                ath = r.get("ath_multiplier", 1)
                current = r.get("current_multiplier", 1)
                min_price = r.get("min_price_multiplier", 0)
                pnl, exit_type = _exit_pnl(ath, current, tp_mult, sl_mult, min_price)
                total_pnl += pnl
                if exit_type == "tp":
                    tp_hits += 1
                elif exit_type == "sl":
                    sl_hits += 1
                else:
                    holds += 1
            n = len(results)
            avg_pnl = total_pnl / n
            win_rate = tp_hits / n
            score = avg_pnl + (win_rate * 15) - (holds / n * 10)
            if score > best_score or (score == best_score and avg_pnl > best_pnl):
                best_score = score
                best_pnl = avg_pnl
                best_tp = tp_pct
                best_sl = sl_pct
    n = len(results)
    best_tp_mult = 1 + best_tp / 100
    best_sl_mult = 1 + best_sl / 100
    final_tp = final_sl = final_hold = 0
    for r in results:
        _, exit_type = _exit_pnl(r.get("ath_multiplier", 1),
                                  r.get("current_multiplier", 1),
                                  best_tp_mult, best_sl_mult,
                                  r.get("min_price_multiplier", 0))
        if exit_type == "tp":
            final_tp += 1
        elif exit_type == "sl":
            final_sl += 1
        else:
            final_hold += 1
    return {
        "optimal_tp": best_tp,
        "optimal_sl": best_sl,
        "expected_pnl": round(best_pnl, 1),
        "win_rate": round(final_tp / n * 100, 1),
        "tp_hits": final_tp,
        "sl_hits": final_sl,
        "holds": final_hold,
    }


def simulate_tp_scenarios(results):
    if not results:
        return []
    optimal = calculate_optimal_tp_sl(results)
    sl_pct = optimal["optimal_sl"]
    scenarios = []
    for tp_pct in range(5, 201, 5):
        tp_mult = 1 + tp_pct / 100
        sl_mult = 1 + sl_pct / 100
        total_pnl = 0
        tp_hits = 0
        sl_hits = 0
        holds = 0
        hold_better_than_sl = 0
        for r in results:
            ath = r.get("ath_multiplier", 1)
            current = r.get("current_multiplier", 1)
            min_price = r.get("min_price_multiplier", 0)
            pnl, exit_type = _exit_pnl(ath, current, tp_mult, sl_mult, min_price)
            total_pnl += pnl
            if exit_type == "tp":
                tp_hits += 1
            elif exit_type == "sl":
                sl_hits += 1
                hold_pnl = (current - 1) * 100
                if hold_pnl > sl_pct:
                    hold_better_than_sl += 1
            else:
                holds += 1
        n = len(results)
        avg_pnl = total_pnl / n
        scenarios.append({
            "tp": tp_pct, "sl": sl_pct,
            "tp_hits": tp_hits, "sl_hits": sl_hits, "holds": holds,
            "tp_rate": round(tp_hits / n * 100, 1),
            "avg_pnl": round(avg_pnl, 1),
            "hold_better": hold_better_than_sl,
        })
    return scenarios


def simulate_trailing_stop(results):
    if not results:
        return {"fixed_pnl": 0, "trailing_pnl": 0, "trailing_better": 0, "total": 0}
    fixed_total = 0
    trailing_total = 0
    trailing_better = 0
    for r in results:
        ath = r.get("ath_multiplier", 1)
        current = r.get("current_multiplier", 1)
        fixed_sl = 0.90
        if ath >= 4.0:
            fixed_pnl = 300
        elif current <= fixed_sl:
            fixed_pnl = -10
        else:
            fixed_pnl = (current - 1) * 100
        trailing_sl = 0.90
        if ath >= 2.0:
            trailing_sl = max(trailing_sl, 1.0)
        if ath >= 3.0:
            trailing_sl = max(trailing_sl, 1.3)
        if ath >= 4.0:
            trailing_sl = max(trailing_sl, 2.0)
        if ath >= 4.0:
            trailing_pnl = 300
        elif current <= trailing_sl:
            trailing_pnl = (trailing_sl - 1) * 100
        else:
            trailing_pnl = (current - 1) * 100
        fixed_total += fixed_pnl
        trailing_total += trailing_pnl
        if trailing_pnl > fixed_pnl:
            trailing_better += 1
    n = len(results)
    return {
        "fixed_pnl": round(fixed_total / n, 1),
        "trailing_pnl": round(trailing_total / n, 1),
        "trailing_better": trailing_better,
        "total": n,
    }


def get_time_analysis(results):
    if not results:
        return {"hourly": {}, "best_hours": [], "worst_hours": []}
    hourly = {}
    for r in results:
        ts = r.get("signal_time") or r.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            h = dt.hour
        except (ValueError, TypeError):
            continue
        if h not in hourly:
            hourly[h] = {"count": 0, "pumps": 0, "aths": [], "total_pnl": 0}
        ath = r.get("ath_multiplier", r.get("multiplier", 1))
        hourly[h]["count"] += 1
        hourly[h]["aths"].append(ath)
        hourly[h]["total_pnl"] += (ath - 1) * 100
        if ath >= 2.0:
            hourly[h]["pumps"] += 1
    for h in hourly:
        d = hourly[h]
        d["avg_ath"] = round(sum(d["aths"]) / len(d["aths"]), 2) if d["aths"] else 0
        d["pump_rate"] = round(d["pumps"] / d["count"] * 100, 1) if d["count"] > 0 else 0
        d["avg_pnl"] = round(d["total_pnl"] / d["count"], 1) if d["count"] > 0 else 0
        del d["aths"]
        del d["total_pnl"]
    sorted_hours = sorted(hourly.items(), key=lambda x: x[1]["pump_rate"], reverse=True)
    best_hours = [h for h, d in sorted_hours[:3] if d["pump_rate"] >= 40]
    worst_hours = [h for h, d in sorted_hours[-3:] if d["pump_rate"] < 20]
    return {"hourly": hourly, "best_hours": best_hours, "worst_hours": worst_hours}


def calculate_risk_adjusted_tp_sl(results):
    if not results:
        return {"tp": 100, "sl": -10, "score": 0, "win_rate": 0, "avg_pnl": 0}
    best_score = -999
    best_tp = 100
    best_sl = -10
    for tp_pct in range(50, 251, 10):
        for sl_pct in range(-15, -3, 1):
            tp_mult = 1 + tp_pct / 100
            sl_mult = 1 + sl_pct / 100
            wins = 0
            losses = 0
            total_win_pnl = 0
            total_loss_pnl = 0
            for r in results:
                ath = r.get("ath_multiplier", 1)
                current = r.get("current_multiplier", 1)
                if ath >= tp_mult:
                    wins += 1
                    total_win_pnl += tp_pct
                elif current <= sl_mult:
                    losses += 1
                    total_loss_pnl += abs(sl_pct)
                else:
                    hold_pnl = (current - 1) * 100
                    if hold_pnl >= 0:
                        wins += 1
                        total_win_pnl += hold_pnl
                    else:
                        losses += 1
                        total_loss_pnl += abs(hold_pnl)
            n = len(results)
            win_rate = wins / n if n > 0 else 0
            loss_rate = losses / n if n > 0 else 0
            avg_win = total_win_pnl / wins if wins > 0 else 0
            avg_loss = total_loss_pnl / losses if losses > 0 else 0
            score = win_rate * avg_win - loss_rate * avg_loss
            if win_rate < 0.3:
                score *= 0.5
            if score > best_score:
                best_score = score
                best_tp = tp_pct
                best_sl = sl_pct
    return {"tp": best_tp, "sl": best_sl, "score": round(best_score, 1),
            "win_rate": round(win_rate * 100, 1), "avg_pnl": round(best_score, 1)}


def get_performance_report():
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    fresh_start = data.get("fresh_start", "")
    now = datetime.now(timezone.utc).timestamp()
    yesterday = now - 86400

    recent = []
    for r in results:
        if r.get("source") == "collector_sync":
            continue
        ts = r.get("timestamp") or r.get("detected_at", "")
        if not ts or ts == "N/A":
            continue
        try:
            if fresh_start and ts < fresh_start:
                continue
            if ts >= datetime.fromtimestamp(yesterday, tz=timezone.utc).isoformat():
                recent.append(r)
        except Exception:
            continue

    if not recent:
        recent = [r for r in results if r.get("source") != "collector_sync" and r.get("current_multiplier", 0) > 0]
        if fresh_start:
            recent = [r for r in recent if (r.get("timestamp") or "") >= fresh_start]
        # Exclude old re-synced tokens: signal_age=0 = collector_sync, not real signal
        recent = [r for r in recent if r.get("signal_age", 0) > 0][-50:]

    # For TP/SL: only use signals with signal_age > 0 (real signals, not old re-synced)
    all_valid = [r for r in results if r.get("current_multiplier", 0) > 0 and r.get("ath_multiplier", 0) > 0 and r.get("signal_age", 0) > 0]
    if fresh_start:
        all_valid = [r for r in all_valid if (r.get("timestamp") or "") >= fresh_start]

    if not recent and not all_valid:
        return {"total": 0, "wins": 0, "losses": 0, "pending": 0,
                "win_rate": 0, "avg_ath": 0, "best": None, "worst": None,
                "optimal_tp": 50, "optimal_sl": -25, "expected_pnl": 0, "signals": []}

    wins = [r for r in recent if r.get("verdict") in ("PUMP", "STRONG_PUMP", "MEGA_PUMP")]
    losses = [r for r in recent if r.get("verdict") == "DUMP"]
    aths = [r.get("ath_multiplier", 1) for r in recent if r.get("ath_multiplier", 0) > 0]
    avg_ath = sum(aths) / len(aths) if aths else 0
    best = max(recent, key=lambda r: r.get("ath_multiplier", 0)) if recent else None
    worst = min(recent, key=lambda r: r.get("ath_multiplier", 0)) if recent else None
    optimal = calculate_optimal_tp_sl(all_valid if all_valid else recent)

    signals = []
    for r in sorted(recent, key=lambda x: x.get("timestamp", ""), reverse=True):
        ath = r.get("ath_multiplier", 1)
        emoji = "🔥" if ath >= 5 else ("✅" if ath >= 2 else ("😐" if ath >= 1 else "❌"))
        signals.append({"symbol": r.get("symbol", "?"), "ath": ath, "emoji": emoji, "verdict": r.get("verdict", "?")})

    scenarios = simulate_tp_scenarios(all_valid if all_valid else recent)
    trailing = simulate_trailing_stop(all_valid if all_valid else recent)
    time_analysis = get_time_analysis(all_valid if all_valid else recent)
    risk_adjusted = calculate_risk_adjusted_tp_sl(all_valid if all_valid else recent)

    return {"total": len(recent), "wins": len(wins), "losses": len(losses),
            "pending": len(recent) - len(wins) - len(losses),
            "win_rate": round(len(wins) / max(len(recent), 1) * 100, 1),
            "avg_ath": round(avg_ath, 2), "best": best, "worst": worst,
            "optimal_tp": optimal["optimal_tp"], "optimal_sl": optimal["optimal_sl"],
            "expected_pnl": optimal["expected_pnl"],
            "optimal_win_rate": optimal.get("win_rate", 0),
            "tp_hits": optimal.get("tp_hits", 0), "sl_hits": optimal.get("sl_hits", 0),
            "holds": optimal.get("holds", 0),
            "tp_scenarios": scenarios, "signals": signals,
            "trailing": trailing, "time_analysis": time_analysis,
            "risk_adjusted": risk_adjusted, "total_all_data": len(all_valid)}


# ── Compatibility: re-export get_launch_age from utils ──

def get_launch_age(pair: dict) -> Optional[float]:
    try:
        from utils import get_launch_age as _gla
        return _gla(pair)
    except Exception:
        return None


# ── Duplicate check ──

def is_duplicate(address: str) -> bool:
    data = load_data()
    addr_lower = address.lower()
    if addr_lower in {a.lower() for a in data.get("trained_addresses", [])}:
        return True
    for p in data.get("pump_patterns", []):
        if p.get("address", "").lower() == addr_lower:
            return True
    for p in data.get("dump_patterns", []):
        if p.get("address", "").lower() == addr_lower:
            return True
    return False


# ── Honeypot blocklist ──

def save_honeypot_blocklist(honeypot_addresses: set, deployers: set, alerted: set) -> None:
    data = load_data()
    data["honeypot_addresses"] = list(honeypot_addresses)
    data["blocked_deployers"] = list(deployers)
    data["alerted_coins"] = list(alerted)
    save_data(data)


def load_honeypot_blocklist() -> tuple:
    data = load_data()
    return (
        set(data.get("honeypot_addresses", [])),
        set(data.get("blocked_deployers", [])),
        set(data.get("alerted_coins", [])),
    )


def purge_honeypot_patterns(honeypot_addresses: set) -> dict:
    if not honeypot_addresses:
        return {"moved": 0}
    data = load_data()
    hp_lower = {a.lower() for a in honeypot_addresses}
    moved = 0
    new_pump = []
    for p in data.get("pump_patterns", []):
        if p.get("address", "").lower() in hp_lower:
            data.setdefault("dump_patterns", []).append(p)
            moved += 1
        else:
            new_pump.append(p)
    data["pump_patterns"] = new_pump[-MAX_PUMP_PATTERNS:]
    data["dump_patterns"] = data.get("dump_patterns", [])[-MAX_DUMP_PATTERNS:]
    save_data(data)
    return {"moved": moved}


# ── Missed pump recording ──

def record_missed_pump(address: str, symbol: str, features: dict, ath_multiplier: float) -> None:
    data = load_data()
    data.setdefault("missed_pumps", []).append({
        "address": address,
        "symbol": symbol,
        "features": features,
        "ath_multiplier": ath_multiplier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    data["missed_pumps"] = data["missed_pumps"][-200:]
    save_data(data)


# ── Signal criteria ──

def get_signal_criteria() -> dict:
    data = load_data()
    criteria = data.get("model", {}).get("signal_criteria", {})
    merged = dict(DEFAULT_SIGNAL_CRITERIA)
    merged.update(criteria)
    return merged


def compute_signal_criteria() -> dict:
    data = load_data()
    pumps = data.get("pump_patterns", [])
    dumps = data.get("dump_patterns", [])
    criteria = dict(DEFAULT_SIGNAL_CRITERIA)

    if len(pumps) >= 5:
        bsr_vals = [p.get("features", {}).get("buy_sell_ratio", 0) for p in pumps if p.get("features", {}).get("buy_sell_ratio", 0) > 0]
        if bsr_vals:
            criteria["min_bsr"] = round(sorted(bsr_vals)[len(bsr_vals) // 2], 2)

        holders_vals = [p.get("features", {}).get("holders", 0) for p in pumps if p.get("features", {}).get("holders", 0) > 0]
        if holders_vals:
            med = int(sorted(holders_vals)[len(holders_vals) // 2])
            criteria["min_holders"] = min(med, 10)  # Cap at 10 (winners have median 10)

        wallets_vals = [p.get("features", {}).get("unique_wallets", 0) for p in pumps if p.get("features", {}).get("unique_wallets", 0) > 0]
        if wallets_vals:
            med = int(sorted(wallets_vals)[len(wallets_vals) // 2])
            criteria["min_wallets"] = min(med, 15)  # Cap at 15

        liq_vals = [p.get("features", {}).get("initial_liq_usd", 0) for p in pumps if p.get("features", {}).get("initial_liq_usd", 0) > 0]
        if liq_vals:
            criteria["min_liq"] = round(sorted(liq_vals)[len(liq_vals) // 2], 0)

    criteria["updated_at"] = datetime.now(timezone.utc).isoformat()
    criteria["sample_size"] = len(pumps)

    data.setdefault("model", {})["signal_criteria"] = criteria
    save_data(data)
    return criteria


# ── Auto-learn update ──

def auto_learn_update() -> dict:
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    if len(results) < 20:
        return {"total_recent": len(results)}

    recent = results[-50:]
    wins = [r for r in recent if r.get("verdict") in ("PUMP", "STRONG_PUMP", "MEGA_PUMP")]
    losses = [r for r in recent if r.get("verdict") in ("DUMP", "DEAD")]
    total = len(recent)
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0

    def avg(key, lst):
        vals = [r.get(key, 0) for r in lst if r.get(key, 0) is not None and r.get(key, 0) > 0]
        return round(sum(vals) / len(vals), 2) if vals else 0

    insights = {
        "total_recent": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win_bsr": avg("buy_sell_ratio", wins),
        "avg_loss_bsr": avg("buy_sell_ratio", losses),
        "avg_win_holders": avg("holders", wins),
        "avg_loss_holders": avg("holders", losses),
        "avg_win_liq": avg("initial_liq_usd", wins),
        "avg_loss_liq": avg("initial_liq_usd", losses),
        "avg_win_lp_locked": avg("lp_locked_pct", wins),
        "avg_loss_lp_locked": avg("lp_locked_pct", losses),
    }

    data.setdefault("model", {})["auto_learn_insights"] = insights
    save_data(data)
    return insights


# ── Learn divergence point ──

def learn_divergence_point() -> dict:
    data = load_data()
    results = data.get("model", {}).get("signal_results", [])
    if len(results) < 10:
        return {}

    pumps = [r for r in results if r.get("verdict") in ("PUMP", "STRONG_PUMP", "MEGA_PUMP") and r.get("signal_age", 0) > 0]
    dumps = [r for r in results if r.get("verdict") in ("DUMP", "DEAD") and r.get("signal_age", 0) > 0]

    if not pumps or not dumps:
        return {}

    pump_ages = [r["signal_age"] for r in pumps]
    dump_ages = [r["signal_age"] for r in dumps]

    pump_avg = sum(pump_ages) / len(pump_ages)
    dump_avg = sum(dump_ages) / len(dump_ages)

    pump_set = set(int(a // 60) for a in pump_ages)
    dump_set = set(int(a // 60) for a in dump_ages)
    overlap = pump_set & dump_set
    total = pump_set | dump_set
    separation = 1.0 - (len(overlap) / len(total)) if total else 0

    optimal_delay = (pump_avg + dump_avg) / 2

    return {
        "optimal_confirm_delay": optimal_delay,
        "pump_avg_age": round(pump_avg, 1),
        "dump_avg_age": round(dump_avg, 1),
        "separation_score": round(separation, 3),
        "pump_count": len(pumps),
        "dump_count": len(dumps),
    }


# ── Daily report ──

def get_daily_report() -> str:
    data = load_data()
    results = data.get("signal_results", [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_signals = [r for r in results if (r.get("timestamp") or "")[:10] == today]

    total = len(today_signals)
    pumps = sum(1 for r in today_signals if r.get("verdict") in ("PUMP", "STRONG_PUMP", "MEGA_PUMP"))
    aths = [r.get("ath_multiplier", 0) for r in today_signals if r.get("ath_multiplier", 0) > 0]
    avg_ath = round(sum(aths) / len(aths), 1) if aths else 0
    best = max(aths) if aths else 0

    lines = [
        f"📊 Daily Report — {today}",
        f"Signals: {total} | Pumps: {pumps} | Win Rate: {round(pumps/total*100,1) if total else 0}%",
        f"Avg ATH: {avg_ath}x | Best: {best}x",
    ]
    for r in today_signals[-5:]:
        sym = r.get("symbol", "?")
        ath = r.get("ath_multiplier", 0)
        v = r.get("verdict", "?")
        lines.append(f"  {sym}: {ath}x ({v})")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# CHANNEL INTELLIGENCE — কোন চ্যানেল থেকে সবচেয়ে ভালো সিগন্যাল আসে
# ══════════════════════════════════════════════════════════════

def record_channel_signal(channel_id: int, channel_name: str, symbol: str,
                          address: str, features: dict, signal_type: str = "") -> None:
    """Record a signal from a specific channel for channel-level analysis."""
    data = load_data()
    ch_key = str(channel_id)
    ch_stats = data.setdefault("channel_stats", {}).setdefault(ch_key, {
        "name": channel_name,
        "total_signals": 0,
        "winners": 0,
        "mega_winners": 0,
        "losers": 0,
        "pending": 0,
        "ath_sum": 0.0,
        "best_ath": 0.0,
        "best_symbol": "",
        "signal_types": {},
        "recent_signals": [],
    })

    ch_stats["total_signals"] += 1
    ch_stats["name"] = channel_name

    # Track signal types (KOTH, FDV_SURGE, KOL_FOMO, etc.)
    if signal_type:
        st = ch_stats.setdefault("signal_types", {})
        st[signal_type] = st.get(signal_type, 0) + 1

    # Store recent signal (keep last 100)
    recent = ch_stats.setdefault("recent_signals", [])
    recent.append({
        "symbol": symbol,
        "address": address,
        "signal_type": signal_type,
        "features": {k: v for k, v in features.items() if isinstance(v, (int, float, str, bool))},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    ch_stats["recent_signals"] = recent[-100:]

    save_data(data)


def update_channel_outcome(channel_id: int, address: str, ath_multiplier: float,
                           current_multiplier: float, verdict: str) -> None:
    """Update channel stats when a signal outcome is known."""
    data = load_data()
    ch_key = str(channel_id)
    ch_stats = data.get("channel_stats", {}).get(ch_key)
    if not ch_stats:
        return

    if verdict in ("PUMP", "STRONG_PUMP", "MEGA_PUMP"):
        ch_stats["winners"] += 1
        if ath_multiplier >= 50:
            ch_stats["mega_winners"] += 1
    elif verdict in ("DUMP", "DEAD"):
        ch_stats["losers"] += 1
    else:
        ch_stats["pending"] += 1

    ch_stats["ath_sum"] += ath_multiplier
    if ath_multiplier > ch_stats.get("best_ath", 0):
        ch_stats["best_ath"] = ath_multiplier
        ch_stats["best_symbol"] = address[:8]

    save_data(data)


def get_channel_weights() -> dict:
    """Calculate channel reliability weights based on historical performance.
    Returns {channel_id: weight} where weight ranges from 0.5 to 2.0.
    High-performing channels get bonus weight, low-performing get penalty."""
    data = load_data()
    ch_stats = data.get("channel_stats", {})
    weights = {}

    for ch_key, stats in ch_stats.items():
        total = stats.get("total_signals", 0)
        winners = stats.get("winners", 0)
        mega = stats.get("mega_winners", 0)
        losers = stats.get("losers", 0)

        if total < 5:
            weights[ch_key] = 1.0  # Not enough data, neutral
            continue

        win_rate = winners / total
        mega_rate = mega / total
        loss_rate = losers / total

        # Score: win_rate boosts, loss_rate penalizes, mega gets extra bonus
        score = 1.0 + (win_rate * 0.5) + (mega_rate * 1.0) - (loss_rate * 0.3)
        weights[ch_key] = round(max(0.5, min(2.0, score)), 2)

    return weights


def get_channel_stats_report() -> str:
    """Generate a detailed channel performance report."""
    data = load_data()
    ch_stats = data.get("channel_stats", {})
    weights = get_channel_weights()

    if not ch_stats:
        return "📊 No channel data yet. Signals will be tracked after fresh start."

    lines = ["📊 Channel Performance Report", "=" * 40]

    # Sort by win rate
    ranked = []
    for ch_key, stats in ch_stats.items():
        total = stats.get("total_signals", 0)
        winners = stats.get("winners", 0)
        mega = stats.get("mega_winners", 0)
        losers = stats.get("losers", 0)
        ath_sum = stats.get("ath_sum", 0)
        best_ath = stats.get("best_ath", 0)
        name = stats.get("name", f"Channel {ch_key}")
        weight = weights.get(ch_key, 1.0)
        win_rate = round(winners / total * 100, 1) if total else 0
        avg_ath = round(ath_sum / total, 1) if total else 0
        ranked.append({
            "name": name, "total": total, "winners": winners,
            "mega": mega, "losers": losers, "win_rate": win_rate,
            "avg_ath": avg_ath, "best_ath": best_ath, "weight": weight,
            "signal_types": stats.get("signal_types", {}),
        })

    ranked.sort(key=lambda x: (-x["win_rate"], -x["best_ath"]))

    for i, ch in enumerate(ranked, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        lines.append(f"\n{medal} {ch['name']}")
        lines.append(f"   Signals: {ch['total']} | Winners: {ch['winners']} ({ch['win_rate']}%) | Mega: {ch['mega']}")
        lines.append(f"   Losers: {ch['losers']} | Avg ATH: {ch['avg_ath']}x | Best: {ch['best_ath']:.1f}x")
        lines.append(f"   Weight: {ch['weight']}x {'⚡ boosted' if ch['weight'] > 1.2 else '⚠️ penalized' if ch['weight'] < 0.8 else 'neutral'}")

        # Signal types breakdown
        if ch["signal_types"]:
            st_parts = [f"{k}={v}" for k, v in sorted(ch["signal_types"].items(), key=lambda x: -x[1])]
            lines.append(f"   Types: {', '.join(st_parts[:5])}")

    # Overall summary
    total_all = sum(ch["total"] for ch in ranked)
    winners_all = sum(ch["winners"] for ch in ranked)
    overall_wr = round(winners_all / total_all * 100, 1) if total_all else 0
    best_channel = ranked[0] if ranked else None

    lines.append(f"\n{'=' * 40}")
    lines.append(f"Total: {total_all} signals | {winners_all} winners ({overall_wr}%)")
    if best_channel:
        lines.append(f"Best channel: {best_channel['name']} ({best_channel['win_rate']}% win rate)")

    return "\n".join(lines)


def analyze_channel_combos() -> dict:
    """Analyze which signal types from which channels perform best.
    Returns insights for optimizing signal collection."""
    data = load_data()
    ch_stats = data.get("channel_stats", {})

    combo_scores = {}  # (channel, signal_type) -> performance

    for ch_key, stats in ch_stats.items():
        name = stats.get("name", f"Channel {ch_key}")
        total = stats.get("total_signals", 0)
        winners = stats.get("winners", 0)
        if total < 3:
            continue

        # Overall channel score
        wr = winners / total
        combo_scores[f"{name} (all)"] = {
            "total": total,
            "win_rate": round(wr * 100, 1),
            "score": round(wr * 2 + (stats.get("mega_winners", 0) / total), 2),
        }

        # Per signal type
        for stype, count in stats.get("signal_types", {}).items():
            if count < 2:
                continue
            combo_scores[f"{name} ({stype})"] = {
                "total": count,
                "win_rate": "N/A",
                "score": 0,
            }

    return combo_scores
