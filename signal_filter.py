import logging
import json
import os
from datetime import datetime, timezone
from typing import Optional
from config import config
from learner import load_data, save_data, _hash_address

logger = logging.getLogger("signal_filter")

GOLDEN_FILE = "./golden_patterns.json"
BLACKLIST_FILE = "./blacklist_patterns.json"

class SignalFilter:
    def __init__(self):
        self.golden_patterns = self._load_golden()
        self.blacklist = self._load_blacklist()
        self.min_threshold = 0.60
        self.onchain_weight = 0.60
        self.social_weight = 0.30
        self.timing_weight = 0.10

    def _load_golden(self) -> dict:
        if os.path.exists(GOLDEN_FILE):
            try:
                with open(GOLDEN_FILE, "r") as f:
                    return json.load(f)
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

    def _save_golden(self):
        with open(GOLDEN_FILE, "w") as f:
            json.dump(self.golden_patterns, f, indent=2)

    def _save_blacklist(self):
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(self.blacklist, f, indent=2)

    def calculate_onchain_score(self, pattern: dict, model: dict, ai_score: float) -> float:
        score = ai_score
        if 5000 <= pattern.get("mcap", 0) <= 500000:
            score += 0.05
        if pattern.get("liquidity", 0) >= 2000:
            score += 0.05
        if pattern.get("vol_liq_ratio", 0) >= 0.2:
            score += 0.05
        return min(1.0, score)

    def calculate_timing_score(self, age_seconds: float) -> float:
        if 120 <= age_seconds <= 600:
            return 1.0
        elif 60 <= age_seconds <= 1200:
            return 0.7
        elif age_seconds < 60:
            return 0.4
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
                best_match = max(best_match, score / checks)
        return best_match

    def should_signal(
        self,
        address: str,
        pattern: dict,
        ai_score: float,
        social_score: float,
        age_seconds: float,
    ) -> tuple:
        if self.is_blacklisted(address):
            return False, 0.0, "🚫 Blacklisted pattern"

        onchain = self.calculate_onchain_score(pattern, {}, ai_score)
        golden_match = self.is_golden_match(pattern)
        if golden_match > 0:
            onchain = max(onchain, golden_match)
        timing = self.calculate_timing_score(age_seconds)

        final_score = (
            onchain * self.onchain_weight
            + social_score * self.social_weight
            + timing * self.timing_weight
        )

        if final_score < self.min_threshold:
            return False, final_score, f"Below threshold ({final_score:.2f} < {self.min_threshold})"

        return True, final_score, "Signal candidate"

    def promote_to_golden(self, symbol: str, pattern: dict, multiplier: float):
        if multiplier < 5.0:
            return
        found = False
        for gp in self.golden_patterns.get("patterns", []):
            if gp.get("symbol") == symbol:
                gp["count"] = gp.get("count", 0) + 1
                gp["max_multiplier"] = max(gp.get("max_multiplier", 0), multiplier)
                gp["avg_multiplier"] = (gp.get("avg_multiplier", 0) + multiplier) / 2
                found = True
                break
        if not found:
            self.golden_patterns.setdefault("patterns", []).append({
                "symbol": symbol,
                "count": 1,
                "max_multiplier": multiplier,
                "avg_multiplier": multiplier,
                "mcap_range": [
                    max(0, pattern.get("mcap", 0) * 0.5),
                    pattern.get("mcap", 0) * 1.5
                ] if pattern.get("mcap", 0) > 0 else [0, 100000],
                "liquidity_range": [
                    max(0, pattern.get("liquidity", 0) * 0.5),
                    pattern.get("liquidity", 0) * 1.5
                ] if pattern.get("liquidity", 0) > 0 else [0, 50000],
            })
        self._save_golden()
        logger.info(f"🌟 Golden pattern updated: {symbol} ({self.golden_patterns['patterns'][-1].get('count', 1)} signals)")

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
            self.add_to_blacklist(address, f"3+ dumps for address")
        elif fails >= 3:
            self.add_to_blacklist(address, f"3+ dumps for {symbol}")
        elif successes >= 5 and all(r["multiplier"] >= 5.0 for r in recent if r["verdict"] != "DUMP"):
            self.promote_to_golden(symbol, pattern, max(r["multiplier"] for r in recent))

        from learner import _update_model
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
        }
