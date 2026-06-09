"""
signal_filter.py — Cleaned single-class version
Fixes:
1. Removed duplicate SignalFilter class (was 2 classes in one file, second one won)
2. min_threshold now reads from env (SIGNAL_MIN_THRESHOLD)
3. effective_threshold returns user-set /threshold value, not min_threshold
4. User /threshold always wins over hardcoded floor
"""

import logging
import json
import os
from datetime import datetime, timezone
from typing import Optional
from config import config
from learner import load_data, save_data, _hash_address, _update_model

logger = logging.getLogger("signal_filter")

GOLDEN_FILE = "./golden_patterns.json"
BLACKLIST_FILE = "./blacklist_patterns.json"


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


class SignalFilter:
    def __init__(self):
        self.golden_patterns = self._load_golden()
        self.blacklist = self._load_blacklist()
        self.min_threshold = _env_float("SIGNAL_MIN_THRESHOLD", 0.65)
        self.warmup_min_threshold = _env_float("SIGNAL_WARMUP_THRESHOLD", 0.40)
        self.pre_migration_threshold = _env_float("SIGNAL_PRE_MIGRATION_THRESHOLD", 0.45)
        self.warmup_signal_count = int(os.getenv("SIGNAL_WARMUP_COUNT", "20"))
        self.onchain_weight = 0.45
        self.social_weight = 0.30
        self.timing_weight = 0.10
        self.whale_weight = 0.10
        self.safety_weight = 0.05
        self.user_threshold: Optional[float] = None

    def set_user_threshold(self, value: float) -> None:
        self.user_threshold = max(0.01, min(1.0, float(value)))
        logger.info(f"🎯 SignalFilter user_threshold = {self.user_threshold:.2f}")

    def _warmup_active(self) -> bool:
        try:
            data = load_data()
            pump_count = len(data.get("pump_patterns", []))
            if pump_count >= self.warmup_signal_count:
                return False
            results = data.get("model", {}).get("signal_results", [])
            return len(results) < self.warmup_signal_count
        except Exception:
            return True

    def effective_threshold(self, pre_migration: bool = False) -> float:
        if pre_migration:
            return self.pre_migration_threshold
        if self.user_threshold is not None:
            return self.user_threshold
        return self.warmup_min_threshold if self._warmup_active() else self.min_threshold

    def _load_golden(self) -> dict:
        if os.path.exists(GOLDEN_FILE):
            try:
                with open(GOLDEN_FILE, "r") as f:
                    data = json.load(f)
                    logger.info(f"🌟 Golden patterns loaded: {len(data.get('patterns', []))}")
                    return data
            except Exception:
                pass
        return {"patterns": [], "min_count": 5, "min_multiplier": 5.0}

    def _load_blacklist(self) -> dict:
        if os.path.exists(BLACKLIST_FILE):
            try:
                with open(BLACKLIST_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"patterns": [], "max_fails": 3}

    def reload_golden(self):
        self.golden_patterns = self._load_golden()
        logger.info(f"🔄 Golden patterns reloaded: {len(self.golden_patterns.get('patterns', []))}")

    def _save_golden(self):
        with open(GOLDEN_FILE, "w") as f:
            json.dump(self.golden_patterns, f, indent=2)

    def _save_blacklist(self):
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(self.blacklist, f, indent=2)

    def calculate_onchain_score(self, pattern: dict, model: dict, ai_score: float) -> float:
        score = ai_score
        mcap = pattern.get("mcap", 0)
        liq = pattern.get("liquidity", 0)
        vol_liq = pattern.get("vol_liq_ratio", 0)
        buy_count = pattern.get("buy_count", 0)
        buy_sell = pattern.get("buy_sell_ratio", 0)

        if 3000 <= mcap <= 1000000:
            score += 0.05
        if liq >= 1000:
            score += 0.05
        if vol_liq >= 0.1:
            score += 0.05

        if buy_count >= 20 and buy_sell >= 1.5:
            score += 0.15
        elif buy_count >= 10 and buy_sell >= 1.2:
            score += 0.08
        elif buy_count >= 5 and buy_sell >= 1.0:
            score += 0.04

        return min(1.0, score)

    def calculate_timing_score(self, age_seconds: float) -> float:
        if 60 <= age_seconds <= 300:
            return 1.0
        elif 30 <= age_seconds <= 600:
            return 0.85
        elif age_seconds < 30:
            return 0.5
        elif age_seconds <= 1200:
            return 0.6
        else:
            return 0.3

    def is_blacklisted(self, address: str) -> bool:
        h = _hash_address(address)
        return h in self.blacklist.get("patterns", [])

    def is_golden_match(self, pattern: dict) -> float:
        best_match = 0.0
        for gp in self.golden_patterns.get("patterns", []):
            score = 0
            checks = 0

            if "mcap_range" in gp:
                checks += 1
                lo, hi = gp["mcap_range"]
                if lo <= pattern.get("mcap", 0) <= hi:
                    score += 1

            if "liquidity_range" in gp:
                checks += 1
                lo, hi = gp["liquidity_range"]
                if lo <= pattern.get("liquidity", 0) <= hi:
                    score += 1

            if checks > 0:
                match_ratio = score / checks
                avg_multi = gp.get("avg_multiplier", 3.0)
                boost = match_ratio * min(0.30, (avg_multi / 10.0))
                best_match = max(best_match, boost)

        return best_match

    def should_signal(
        self,
        address: str,
        pattern: dict,
        ai_score: float,
        social_score: float,
        age_seconds: float,
        whale_data: Optional[dict] = None,
        safety_data: Optional[dict] = None,
        pre_migration: bool = False,
    ) -> tuple:
        if self.is_blacklisted(address):
            return False, 0.0, "🚫 Blacklisted pattern"

        try:
            social_score = float(social_score) if social_score is not None else 0.0
        except (TypeError, ValueError):
            social_score = 0.0

        try:
            ai_score = float(ai_score) if ai_score is not None else 0.0
        except (TypeError, ValueError):
            ai_score = 0.0

        onchain = self.calculate_onchain_score(pattern, {}, ai_score)

        golden_boost = self.is_golden_match(pattern)
        onchain = min(1.0, onchain + golden_boost)

        timing = self.calculate_timing_score(age_seconds)

        whale_score = 0.0
        if whale_data:
            whale_buys = whale_data.get("whale_buys", 0)
            whale_sells = whale_data.get("whale_sells", 0)
            if whale_buys > whale_sells and whale_buys >= 2:
                whale_score = min(1.0, whale_buys * 0.15)
            elif whale_buys > 0:
                whale_score = 0.3

        safety_score = 1.0
        if safety_data:
            if safety_data.get("is_manipulated"):
                safety_score = 0.0
            elif safety_data.get("price_impact_pct", 0) > 10:
                safety_score = 0.2
            elif safety_data.get("price_impact_pct", 0) > 5:
                safety_score = 0.5
            top10 = safety_data.get("top10_holder_pct", 0)
            if top10 > 80:
                safety_score = min(safety_score, 0.1)
            elif top10 > 60:
                safety_score = min(safety_score, 0.5)

        if social_score < 0.01:
            w_onchain = min(1.0, self.onchain_weight + self.social_weight)
            w_social = 0.0
            w_timing = self.timing_weight
            w_whale = self.whale_weight
            w_safety = self.safety_weight
        else:
            w_onchain = self.onchain_weight
            w_social = self.social_weight
            w_timing = self.timing_weight
            w_whale = self.whale_weight
            w_safety = self.safety_weight

        final_score = (
            onchain * w_onchain
            + social_score * w_social
            + timing * w_timing
            + whale_score * w_whale
            + safety_score * w_safety
        )

        thr = self.effective_threshold(pre_migration=pre_migration)
        if final_score < thr:
            return False, final_score, f"Below threshold ({final_score:.2f} < {thr:.2f})"

        return True, final_score, "Signal candidate"

    def promote_to_golden(self, symbol: str, pattern: dict, multiplier: float):
        if multiplier < 5.0:
            return

        found = False
        for gp in self.golden_patterns.get("patterns", []):
            if gp.get("symbol") == symbol:
                gp["count"] = gp.get("count", 0) + 1
                gp["max_multiplier"] = max(gp.get("max_multiplier", 0), multiplier)
                gp["avg_multiplier"] = (gp.get("avg_multiplier", multiplier) + multiplier) / 2
                found = True
                break

        if not found:
            mcap = pattern.get("mcap", 0)
            liq = pattern.get("liquidity", 0)
            self.golden_patterns.setdefault("patterns", []).append({
                "symbol": symbol,
                "count": 1,
                "max_multiplier": multiplier,
                "avg_multiplier": multiplier,
                "mcap_range": [
                    max(0, mcap * 0.3),
                    mcap * 3.0
                ] if mcap > 0 else [0, 500000],
                "liquidity_range": [
                    max(0, liq * 0.3),
                    liq * 3.0
                ] if liq > 0 else [0, 100000],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        self._save_golden()
        count = len(self.golden_patterns.get("patterns", []))
        logger.info(f"🌟 Golden pattern updated: {symbol} {multiplier}x (total: {count})")

    def add_to_blacklist(self, address: str, reason: str = ""):
        h = _hash_address(address)
        if h not in self.blacklist.setdefault("patterns", []):
            self.blacklist["patterns"].append(h)
            self._save_blacklist()
            logger.info(f"🚫 Blacklisted: {address[:8]}... ({reason})")

    def record_signal_result(self, address: str, symbol: str, pattern: dict, multiplier: float, social_score: float = 0):
        data = load_data()
        model = data["model"]
        results = model.setdefault("signal_results", [])

        verdict = "DUMP"
        if multiplier >= 5.0:
            verdict = "STRONG_PUMP"
        elif multiplier >= 3.0:
            verdict = "PUMP"

        results.append({
            "address": address,
            "symbol": symbol,
            "verdict": verdict,
            "multiplier": multiplier,
            "social_score": social_score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        results = results[-500:]
        model["signal_results"] = results

        recent = [r for r in results if r["symbol"] == symbol][-5:]
        fails = sum(1 for r in recent if r["verdict"] == "DUMP")
        successes = sum(1 for r in recent if r["verdict"] in ("PUMP", "STRONG_PUMP"))

        same_addr_fails = sum(
            1 for r in results[-10:]
            if r["address"] == address and r["verdict"] == "DUMP"
        )

        if same_addr_fails >= 3:
            self.add_to_blacklist(address, "3+ dumps for address")
        elif fails >= 3:
            self.add_to_blacklist(address, f"3+ dumps for {symbol}")
        elif successes >= 3 and all(r["multiplier"] >= 5.0 for r in recent if r["verdict"] != "DUMP"):
            self.promote_to_golden(symbol, pattern, max(r["multiplier"] for r in recent))

        _update_model(data)
        save_data(data)

    def get_stats(self) -> dict:
        data = load_data()
        model = data["model"]
        results = model.get("signal_results", [])
        total = len(results)
        pumps = sum(1 for r in results if r["verdict"] in ("PUMP", "STRONG_PUMP"))
        strong = sum(1 for r in results if r["verdict"] == "STRONG_PUMP")

        return {
            "total_signals": total,
            "successful": pumps,
            "strong_pumps": strong,
            "win_rate": round(pumps / total * 100, 1) if total > 0 else 0,
            "strong_rate": round(strong / total * 100, 1) if total > 0 else 0,
            "golden_count": len(self.golden_patterns.get("patterns", [])),
            "blacklist_count": len(self.blacklist.get("patterns", [])),
            "warmup_active": self._warmup_active(),
            "effective_threshold": self.effective_threshold(),
        }
