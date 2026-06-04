import json
import os
import hashlib
import statistics
from datetime import datetime, timezone
from typing import Optional
from config import config

DATA_FILE = config.data_file

DEFAULT_DATA = {
    "pump_patterns": [],
    "dump_patterns": [],
    "launch_patterns": [],
    "trained_addresses": [],
    "signals": [],
    "model": {
        "avg_pump_mcap": 0.0,
        "avg_pump_liquidity": 0.0,
        "avg_pump_volume_h1": 0.0,
        "avg_pump_buys_h1": 0.0,
        "avg_pump_price_change_5m": 0.0,
        "avg_pump_age_at_signal": 0.0,
        "best_hours": {},
        "hourly_success_rate": {},
        "total_signals": 0,
        "correct_signals": 0,
        "accuracy": 0.0,
        "threshold": 0.50,
        "signal_age_min": 60,
        "signal_age_max": 600,
        "launch_avg_buys": 0.0,
        "launch_avg_wallets": 0.0,
        "launch_avg_volume": 0.0,
    }
}

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                for key in DEFAULT_DATA:
                    if key not in data:
                        data[key] = DEFAULT_DATA[key]
                for key in DEFAULT_DATA["model"]:
                    if key not in data["model"]:
                        data["model"][key] = DEFAULT_DATA["model"][key]
                return data
        except Exception:
            pass
    return DEFAULT_DATA.copy()

def save_data(data: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _hash_address(address: str) -> str:
    return hashlib.md5(address.lower().encode()).hexdigest()

def is_duplicate(address: str) -> bool:
    data = load_data()
    return _hash_address(address) in data.get("trained_addresses", [])

def _mark_trained(data: dict, address: str) -> None:
    h = _hash_address(address)
    if h not in data["trained_addresses"]:
        data["trained_addresses"].append(h)

def get_launch_age(pair: dict) -> Optional[float]:
    try:
        created_at = pair.get("pairCreatedAt")
        if created_at:
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            return (now_ms - int(created_at)) / 1000
    except Exception:
        pass
    return None

def verify_pump(pair: dict, multiplier_threshold: float = 3.0) -> tuple:
    try:
        h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
        h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        best = max(h1, h6, h24)
        multiplier = 1 + best / 100
        return multiplier >= multiplier_threshold, round(multiplier, 2)
    except Exception:
        return False, 0.0

def extract_pattern(pair: dict, age_seconds: Optional[float] = None) -> Optional[dict]:
    try:
        if age_seconds is None:
            age_seconds = get_launch_age(pair) or 0
        return {
            "mcap": float(pair.get("fdv", 0) or 0),
            "liquidity": float(pair.get("liquidity", {}).get("usd", 0) or 0),
            "volume_h1": float(pair.get("volume", {}).get("h1", 0) or 0),
            "volume_m5": float(pair.get("volume", {}).get("m5", 0) or 0),
            "age_seconds": age_seconds,
            "price_change_5m": float(pair.get("priceChange", {}).get("m5", 0) or 0),
            "price_change_1h": float(pair.get("priceChange", {}).get("h1", 0) or 0),
            "hour_of_day": datetime.now(timezone.utc).hour,
            "buys_m5": int(pair.get("txns", {}).get("m5", {}).get("buys", 0) or 0),
            "sells_m5": int(pair.get("txns", {}).get("m5", {}).get("sells", 0) or 0),
            "buys_h1": int(pair.get("txns", {}).get("h1", {}).get("buys", 0) or 0),
            "sells_h1": int(pair.get("txns", {}).get("h1", {}).get("sells", 0) or 0),
        }
    except Exception:
        return None

def learn_pump(coin_info: dict, pair: dict, final_multiplier: float, address: Optional[str] = None, manual: bool = False) -> tuple:
    data = load_data()
    if address and is_duplicate(address):
        return False, "ইতিমধ্যে শেখা আছে! ডুপ্লিকেট।"

    if not manual:
        verified, actual_multi = verify_pump(pair, config.pump_multiplier)
        if not verified:
            return False, f"{config.pump_multiplier}x ভেরিফাই হয়নি (max: {actual_multi}x)"
        final_multiplier = actual_multi

    age = get_launch_age(pair)
    pattern = extract_pattern(pair, age)
    if not pattern:
        return False, "ডেটা পাওয়া যায়নি"

    pattern["symbol"] = coin_info.get("symbol", "???")
    pattern["name"] = coin_info.get("name", "Unknown")
    pattern["address"] = address or ""
    pattern["final_multiplier"] = final_multiplier
    pattern["manual"] = manual
    pattern["timestamp"] = datetime.now(timezone.utc).isoformat()

    data["pump_patterns"].append(pattern)
    data["pump_patterns"] = data["pump_patterns"][-500:]

    if address:
        _mark_trained(data, address)

    _update_model(data)
    save_data(data)
    return True, f"✅ পাম্প শেখা হয়েছে! {final_multiplier}x | মোট: {len(data['pump_patterns'])}"

def learn_dump(coin_info: dict, pair: dict, address: Optional[str] = None, manual: bool = False) -> tuple:
    data = load_data()
    if address and is_duplicate(address):
        return False, "ইতিমধ্যে শেখা আছে! ডুপ্লিকেট।"

    age = get_launch_age(pair)
    pattern = extract_pattern(pair, age)
    if not pattern:
        return False, "ডেটা পাওয়া যায়নি"

    pattern["symbol"] = coin_info.get("symbol", "???")
    pattern["address"] = address or ""
    pattern["final_multiplier"] = 0
    pattern["manual"] = manual
    pattern["timestamp"] = datetime.now(timezone.utc).isoformat()

    data["dump_patterns"].append(pattern)
    data["dump_patterns"] = data["dump_patterns"][-500:]

    if address:
        _mark_trained(data, address)

    save_data(data)
    return True, f"✅ ডাম্প শেখা হয়েছে! মোট: {len(data['dump_patterns'])}"

def _update_model(data: dict) -> None:
    pumps = data["pump_patterns"]
    if len(pumps) < 1:
        return

    model = data["model"]

    mcap_values = [p["mcap"] for p in pumps if p["mcap"] > 0]
    if mcap_values:
        model["avg_pump_mcap"] = statistics.mean(mcap_values)

    liq_values = [p["liquidity"] for p in pumps if p["liquidity"] > 0]
    if liq_values:
        model["avg_pump_liquidity"] = statistics.mean(liq_values)

    vol_values = [p.get("volume_h1", 0) for p in pumps if p.get("volume_h1", 0) > 0]
    if vol_values:
        model["avg_pump_volume_h1"] = statistics.mean(vol_values)

    buys_values = [p.get("buys_h1", 0) for p in pumps if p.get("buys_h1", 0) > 0]
    if buys_values:
        model["avg_pump_buys_h1"] = statistics.mean(buys_values)

    model["avg_pump_price_change_5m"] = statistics.mean([p.get("price_change_5m", 0) for p in pumps])

    successful_ages = [p["age_seconds"] for p in pumps if p.get("age_seconds", 0) > 0 and p.get("final_multiplier", 0) >= 3.0]
    if successful_ages:
        model["avg_pump_age_at_signal"] = statistics.mean(successful_ages)
        model["signal_age_min"] = max(60, min(successful_ages) * 0.5)
        model["signal_age_max"] = min(600, max(successful_ages) * 1.5)

    hour_counts = {}
    hour_success = {}
    for p in pumps:
        h = str(p.get("hour_of_day", 0))
        hour_counts[h] = hour_counts.get(h, 0) + 1
        if p.get("final_multiplier", 0) >= 3.0:
            hour_success[h] = hour_success.get(h, 0) + 1

    model["best_hours"] = hour_counts
    model["hourly_success_rate"] = {h: round(hour_success.get(h, 0) / hour_counts[h], 2) for h in hour_counts if hour_counts[h] > 0}

    total = model.get("total_signals", 0)
    correct = model.get("correct_signals", 0)
    if total >= 5:
        model["accuracy"] = round(correct / total * 100, 1)
        if model["accuracy"] < 40:
            model["threshold"] = min(0.7, model["threshold"] + 0.05)
        elif model["accuracy"] > 70:
            model["threshold"] = max(0.2, model["threshold"] - 0.05)

    launches = data["launch_patterns"]
    if len(launches) >= 3:
        model["launch_avg_buys"] = statistics.mean([l.get("buy_count", 0) for l in launches])
        model["launch_avg_wallets"] = statistics.mean([l.get("unique_wallets", 0) for l in launches])
        model["launch_avg_volume"] = statistics.mean([l.get("volume", 0) for l in launches])

    data["model"] = model

def score_coin(pair: dict, coin_info: dict, age_seconds: Optional[float] = None) -> tuple:
    data = load_data()
    model = data["model"]
    pumps = data["pump_patterns"]
    dumps = data["dump_patterns"]

    if age_seconds is None:
        age_seconds = get_launch_age(pair) or 0
    pattern = extract_pattern(pair, age_seconds)
    if not pattern:
        return 0.0, "ডেটা নেই"

    if len(pumps) < 3:
        score = 0.0
        reasons = []
        if pattern["price_change_5m"] > 5:
            score += 0.25
            reasons.append("৫m মোমেন্টাম ✅")
        buys = pattern["buys_m5"]
        sells = pattern["sells_m5"]
        if buys + sells > 0 and buys / (buys + sells) > 0.6:
            score += 0.25
            reasons.append("Buy pressure ✅")
        if pattern["volume_m5"] > 300:
            score += 0.2
            reasons.append("Volume spike ✅")
        if pattern["liquidity"] > 5000:
            score += 0.2
            reasons.append("লিকুইডিটি ✅")
        if 0 < age_seconds <= 600:
            score += 0.1
            reasons.append("Early launch ✅")
        return round(min(score, 1.0), 2), "⏳ শিখছি | " + " | ".join(reasons)

    score = 0.0
    reasons = []

    if 0 < age_seconds <= 600:
        score += 0.1
        reasons.append("Early launch ✅")

    if model["avg_pump_mcap"] > 0:
        mcap_ratio = pattern["mcap"] / model["avg_pump_mcap"]
        if 0.1 <= mcap_ratio <= 5.0:
            score += 0.2
            reasons.append("MCap ✅")
        else:
            score -= 0.1

    if model["avg_pump_liquidity"] > 0:
        liq_ratio = pattern["liquidity"] / model["avg_pump_liquidity"]
        if 0.1 <= liq_ratio <= 5.0:
            score += 0.15
            reasons.append("লিকুইডিটি ✅")

    if model["avg_pump_volume_h1"] > 0:
        vol_ratio = pattern["volume_h1"] / model["avg_pump_volume_h1"]
        if 0.1 <= vol_ratio <= 5.0:
            score += 0.15
            reasons.append("ভলিউম ✅")

    if model["avg_pump_buys_h1"] > 0 and pattern["buys_h1"] >= model["avg_pump_buys_h1"] * 0.5:
        score += 0.1
        reasons.append("Buy count ✅")

    if pattern["price_change_5m"] > 5:
        score += 0.15
        reasons.append("৫m মোমেন্টাম ✅")
    elif pattern["price_change_5m"] < -15:
        score -= 0.15

    buys = pattern["buys_m5"]
    sells = pattern["sells_m5"]
    if buys + sells > 0:
        buy_ratio = buys / (buys + sells)
        if buy_ratio > 0.55:
            score += 0.15
            reasons.append("Buy pressure ✅")
        elif buy_ratio < 0.3:
            score -= 0.1

    hour = str(pattern["hour_of_day"])
    hourly_success = model.get("hourly_success_rate", {})
    if hourly_success and hour in hourly_success and hourly_success[hour] >= 0.5:
        score += 0.1
        reasons.append("সেরা সময় ✅")

    dump_matches = 0
    for dp in dumps[-100:]:
        if dp["mcap"] > 0 and pattern["mcap"] > 0:
            if abs(dp["mcap"] - pattern["mcap"]) / pattern["mcap"] < 0.25:
                dump_matches += 1
    if dump_matches > 10:
        score -= 0.25
        reasons.append("⚠️ ডাম্প প্যাটার্ন")

    score = max(0.0, min(1.0, score))
    return round(score, 2), " | ".join(reasons) if reasons else "প্যাটার্ন দুর্বল"

def get_signal_age_window() -> tuple:
    data = load_data()
    model = data["model"]
    return model.get("signal_age_min", 60), model.get("signal_age_max", 600)

def record_signal(address: str, symbol: str, score: float, price_at_signal: float, mcap_at_signal: float) -> None:
    data = load_data()
    data["signals"].append({
        "address": address,
        "symbol": symbol,
        "score": score,
        "price_at_signal": price_at_signal,
        "mcap_at_signal": mcap_at_signal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result_multiplier": None,
        "result_checked": False
    })
    data["signals"] = data["signals"][-500:]
    data["model"]["total_signals"] = data["model"].get("total_signals", 0) + 1
    save_data(data)

def update_signal_result(address: str, current_price: float) -> None:
    data = load_data()
    updated = False
    for sig in data["signals"]:
        if sig["address"] == address and not sig["result_checked"]:
            if sig["price_at_signal"] > 0:
                multiplier = current_price / sig["price_at_signal"]
                sig["result_multiplier"] = round(multiplier, 2)
                sig["result_checked"] = True
                if multiplier >= 2.0:
                    data["model"]["correct_signals"] = data["model"].get("correct_signals", 0) + 1
                updated = True
    if updated:
        _update_model(data)
        save_data(data)

def get_stats() -> dict:
    data = load_data()
    model = data["model"]
    checked = [s for s in data["signals"] if s["result_checked"]]
    successful = [s for s in checked if (s.get("result_multiplier") or 0) >= 2.0]
    best_hours = model.get("best_hours", {})
    best_hour = max(best_hours, key=best_hours.get) if best_hours else "N/A"
    manual_pumps = sum(1 for p in data["pump_patterns"] if p.get("manual"))
    manual_dumps = sum(1 for p in data["dump_patterns"] if p.get("manual"))
    age_min = model.get("signal_age_min", 60)
    age_max = model.get("signal_age_max", 600)
    return {
        "pump_patterns": len(data["pump_patterns"]),
        "dump_patterns": len(data["dump_patterns"]),
        "launch_patterns": len(data["launch_patterns"]),
        "manual_pumps": manual_pumps,
        "manual_dumps": manual_dumps,
        "total_signals": len(data["signals"]),
        "checked_signals": len(checked),
        "successful_signals": len(successful),
        "accuracy": model.get("accuracy", 0.0),
        "threshold": model.get("threshold", 0.50),
        "best_hour": best_hour,
        "trained_addresses": len(data.get("trained_addresses", [])),
        "signal_age_min": int(age_min // 60),
        "signal_age_max": int(age_max // 60)
    }

def get_daily_report() -> dict:
    data = load_data()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_signals = [s for s in data["signals"] if s["timestamp"].startswith(today)]
    today_pumps = [p for p in data["pump_patterns"] if p["timestamp"].startswith(today)]
    checked = [s for s in today_signals if s["result_checked"]]
    successful = [s for s in checked if (s.get("result_multiplier") or 0) >= 2.0]
    best = max(checked, key=lambda x: x.get("result_multiplier") or 0) if checked else None
    return {
        "date": today,
        "signals_sent": len(today_signals),
        "pumps_learned": len(today_pumps),
        "checked": len(checked),
        "successful": len(successful),
        "best_signal": best
    }

def score_launch(launch_data: dict) -> tuple:
    data = load_data()
    model = data["model"]
    launches = data["launch_patterns"]

    buys = launch_data.get("buy_count", 0)
    unique = launch_data.get("unique_wallets", 0)
    volume = launch_data.get("volume", 0)
    buy_sell = launch_data.get("buy_sell_ratio", 1)

    if len(launches) < 3:
        score = 0.0
        reasons = []
        if buys > 10:
            score += 0.3
            reasons.append("Buy count ✅")
        if unique > 5:
            score += 0.3
            reasons.append("Unique wallets ✅")
        if volume > 1:
            score += 0.2
            reasons.append("Volume ✅")
        if buy_sell > 2:
            score += 0.2
            reasons.append("Buy pressure ✅")
        return round(min(score, 1.0), 2), "⏳ শিখছি | " + " | ".join(reasons)

    score = 0.0
    reasons = []
    avg_buys = model.get("launch_avg_buys", 0)
    avg_wallets = model.get("launch_avg_wallets", 0)
    avg_volume = model.get("launch_avg_volume", 0)

    if avg_buys > 0 and buys >= avg_buys * 0.7:
        score += 0.25
        reasons.append("Buy count ✅")
    if avg_wallets > 0 and unique >= avg_wallets * 0.7:
        score += 0.25
        reasons.append("Unique wallets ✅")
    if avg_volume > 0 and volume >= avg_volume * 0.5:
        score += 0.2
        reasons.append("Volume ✅")
    if buy_sell > 2:
        score += 0.2
        reasons.append("Buy pressure ✅")

    hour = str(datetime.now(timezone.utc).hour)
    hourly_success = model.get("hourly_success_rate", {})
    if hourly_success and hour in hourly_success and hourly_success[hour] >= 0.5:
        score += 0.1
        reasons.append("সেরা সময় ✅")

    return round(min(score, 1.0), 2), " | ".join(reasons) if reasons else "প্যাটার্ন দুর্বল"

def extract_launch_pattern(transactions: list) -> Optional[dict]:
    try:
        if not transactions:
            return None
        buy_count = 0
        sell_count = 0
        total_volume = 0
        unique_wallets = set()
        for tx in transactions[:20]:
            tx_type = tx.get("type", "")
            source = tx.get("source", "")
            transfers = tx.get("tokenTransfers", [])
            for transfer in transfers:
                amount = float(transfer.get("tokenAmount", 0) or 0)
                wallet = transfer.get("fromUserAccount", "")
                if wallet:
                    unique_wallets.add(wallet)
                total_volume += amount
            if tx_type == "SWAP":
                if source == "PUMP_FUN":
                    buy_count += 1
                else:
                    sell_count += 1
        return {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "unique_wallets": len(unique_wallets),
            "volume": total_volume,
            "buy_sell_ratio": buy_count / max(sell_count, 1),
            "hour_of_day": datetime.now(timezone.utc).hour,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception:
        return None

def learn_pump_with_launch(coin_info: dict, pair: dict, final_multiplier: float, launch_pattern: Optional[dict], address: Optional[str] = None, manual: bool = False) -> tuple:
    data = load_data()
    if address and is_duplicate(address):
        return False, "ডুপ্লিকেট!"

    if not manual:
        verified, actual_multi = verify_pump(pair, config.pump_multiplier)
        if not verified:
            return False, f"{config.pump_multiplier}x ভেরিফাই হয়নি ({actual_multi}x)"
        final_multiplier = actual_multi

    age = get_launch_age(pair)
    pattern = extract_pattern(pair, age)
    if not pattern:
        return False, "ডেটা নেই"
    pattern["symbol"] = coin_info.get("symbol", "???")
    pattern["name"] = coin_info.get("name", "Unknown")
    pattern["address"] = address or ""
    pattern["final_multiplier"] = final_multiplier
    pattern["manual"] = manual
    pattern["timestamp"] = datetime.now(timezone.utc).isoformat()
    data["pump_patterns"].append(pattern)
    data["pump_patterns"] = data["pump_patterns"][-500:]

    if launch_pattern:
        launch_pattern["symbol"] = coin_info.get("symbol", "???")
        launch_pattern["address"] = address or ""
        launch_pattern["final_multiplier"] = final_multiplier
        data["launch_patterns"].append(launch_pattern)
        data["launch_patterns"] = data["launch_patterns"][-500:]

    if address:
        _mark_trained(data, address)
    _update_model(data)
    save_data(data)
    return True, f"✅ পাম্প শেখা! {final_multiplier}x | মোট: {len(data['pump_patterns'])}"
