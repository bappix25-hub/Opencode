import json
import os
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger("pattern_analyzer")

PATTERNS_FILE = os.path.join(os.path.dirname(__file__), "data", "backtest_patterns.json")


@dataclass
class AnalyzedPattern:
    name: str
    condition: dict
    success_rate: float = 0.0
    occurrences: int = 0
    successes: int = 0
    avg_pnl: float = 0.0
    best_tp: float = 50.0
    best_sl: float = -25.0
    confidence: float = 0.0
    last_updated: float = 0.0


class PatternAnalyzer:
    def __init__(self):
        self.patterns: List[dict] = []
        self.market_regimes: Dict[str, dict] = {}
        self.signal_correlations: Dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(PATTERNS_FILE):
                with open(PATTERNS_FILE, "r") as f:
                    data = json.load(f)
                    self.patterns = data.get("patterns", [])
                    self.market_regimes = data.get("market_regimes", {})
                    self.signal_correlations = data.get("signal_correlations", {})
                logger.info(f"Patterns loaded: {len(self.patterns)} patterns")
        except Exception as e:
            logger.error(f"Error loading patterns: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(PATTERNS_FILE), exist_ok=True)
            data = {
                "patterns": self.patterns,
                "market_regimes": self.market_regimes,
                "signal_correlations": self.signal_correlations,
                "saved_at": time.time(),
            }
            with open(PATTERNS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving patterns: {e}")

    def analyze_backtest_results(self, results: List[dict]) -> List[dict]:
        if not results:
            return []

        pattern_conditions = [
            {"liquidity_range": "micro", "liquidity_max": 1000},
            {"liquidity_range": "low", "liquidity_min": 1000, "liquidity_max": 10000},
            {"liquidity_range": "mid", "liquidity_min": 10000, "liquidity_max": 100000},
            {"liquidity_range": "high", "liquidity_min": 100000},
            {"fdv_range": "micro", "fdv_max": 5000},
            {"fdv_range": "low", "fdv_min": 5000, "fdv_max": 50000},
            {"fdv_range": "mid", "fdv_min": 50000, "fdv_max": 500000},
            {"pnl_range": "big_win", "pnl_min": 50},
            {"pnl_range": "win", "pnl_min": 10, "pnl_max": 50},
            {"pnl_range": "loss", "pnl_max": -10},
        ]

        new_patterns = []
        for cond in pattern_conditions:
            matching = [r for r in results if self._matches_condition(r, cond)]
            if len(matching) >= 3:
                wins = [r for r in matching if r["pnl_pct"] > 0]
                win_rate = len(wins) / len(matching) if matching else 0
                avg_pnl = sum(r["pnl_pct"] for r in matching) / len(matching)

                best_tp = 50.0
                best_sl = -25.0
                if wins:
                    best_tp = sum(r.get("best_tp", 50) for r in wins) / len(wins)
                    best_sl = sum(r.get("best_sl", -25) for r in wins) / len(wins)

                pattern = {
                    "name": f"pattern_{len(self.patterns) + len(new_patterns)}",
                    "condition": cond,
                    "success_rate": win_rate,
                    "occurrences": len(matching),
                    "successes": len(wins),
                    "avg_pnl": avg_pnl,
                    "best_tp": best_tp,
                    "best_sl": best_sl,
                    "confidence": min(len(matching) / 20, 1.0),
                    "last_updated": time.time(),
                }
                new_patterns.append(pattern)

        self._update_existing_patterns(results)
        self.patterns.extend(new_patterns)
        self.patterns = self._deduplicate_patterns(self.patterns)
        self.patterns = [p for p in self.patterns if p["occurrences"] >= 3]
        self._save()

        logger.info(f"Pattern analysis: {len(new_patterns)} new patterns, {len(self.patterns)} total")
        return new_patterns

    def _matches_condition(self, result: dict, cond: dict) -> bool:
        liquidity = result.get("liquidity", 0)
        fdv = result.get("fdv", 0)
        pnl = result.get("pnl_pct", 0)

        if "liquidity_max" in cond and liquidity >= cond["liquidity_max"]:
            return False
        if "liquidity_min" in cond and liquidity < cond["liquidity_min"]:
            return False
        if "fdv_max" in cond and fdv >= cond["fdv_max"]:
            return False
        if "fdv_min" in cond and fdv < cond["fdv_min"]:
            return False
        if "pnl_min" in cond and pnl < cond["pnl_min"]:
            return False
        if "pnl_max" in cond and pnl >= cond["pnl_max"]:
            return False

        return True

    def _update_existing_patterns(self, results: List[dict]):
        for pattern in self.patterns:
            cond = pattern["condition"]
            matching = [r for r in results if self._matches_condition(r, cond)]

            if matching:
                wins = [r for r in matching if r["pnl_pct"] > 0]
                pattern["occurrences"] += len(matching)
                pattern["successes"] += len(wins)
                pattern["success_rate"] = pattern["successes"] / pattern["occurrences"]
                pattern["avg_pnl"] = sum(r["pnl_pct"] for r in matching) / len(matching)
                pattern["confidence"] = min(pattern["occurrences"] / 20, 1.0)
                pattern["last_updated"] = time.time()

    def _deduplicate_patterns(self, patterns: List[dict]) -> List[dict]:
        unique = []
        seen_conditions = set()

        for p in patterns:
            cond_key = json.dumps(p["condition"], sort_keys=True)
            if cond_key not in seen_conditions:
                seen_conditions.add(cond_key)
                unique.append(p)

        return unique

    def update_signal_correlations(self, results: List[dict]):
        signal_pairs = {}

        for result in results:
            signals = result.get("signals", [])
            is_win = result.get("pnl_pct", 0) > 0

            for i, s1 in enumerate(signals):
                for s2 in signals[i+1:]:
                    pair = tuple(sorted([s1, s2]))
                    if pair not in signal_pairs:
                        signal_pairs[pair] = {"wins": 0, "losses": 0}
                    if is_win:
                        signal_pairs[pair]["wins"] += 1
                    else:
                        signal_pairs[pair]["losses"] += 1

        for pair, stats in signal_pairs.items():
            total = stats["wins"] + stats["losses"]
            if total >= 3:
                key = "+".join(pair)
                self.signal_correlations[key] = {
                    "win_rate": stats["wins"] / total,
                    "count": total,
                    "avg_pnl": sum(r["pnl_pct"] for r in results if set(pair).issubset(set(r.get("signals", [])))) / total,
                }

        self._save()

    def identify_market_regime(self, price_history_data: dict) -> str:
        if not price_history_data:
            return "unknown"

        total_change = 0
        count = 0
        for addr, history in price_history_data.items():
            if len(history) >= 2:
                first_price = history[0][1]
                last_price = history[-1][1]
                if first_price > 0:
                    change = (last_price - first_price) / first_price * 100
                    total_change += change
                    count += 1

        if count == 0:
            return "unknown"

        avg_change = total_change / count

        if avg_change > 20:
            return "bull"
        elif avg_change < -20:
            return "bear"
        else:
            return "sideways"

    def get_recommended_patterns(self, min_confidence: float = 0.3) -> List[dict]:
        return [
            p for p in self.patterns
            if p["success_rate"] > 0.5 and p["confidence"] >= min_confidence
        ]

    def get_pattern_for_conditions(self, liquidity: float, fdv: float) -> Optional[dict]:
        for pattern in self.patterns:
            cond = pattern["condition"]
            if self._matches_condition({"liquidity": liquidity, "fdv": fdv, "pnl_pct": 0}, cond):
                if pattern["success_rate"] > 0.5 and pattern["confidence"] >= 0.3:
                    return pattern
        return None

    def get_accuracy_report(self) -> dict:
        total_patterns = len(self.patterns)
        profitable = sum(1 for p in self.patterns if p["success_rate"] > 0.5)
        high_conf = sum(1 for p in self.patterns if p["confidence"] >= 0.5)

        return {
            "total_patterns": total_patterns,
            "profitable_patterns": profitable,
            "high_confidence": high_conf,
            "avg_success_rate": sum(p["success_rate"] for p in self.patterns) / total_patterns if total_patterns > 0 else 0,
            "signal_correlations": len(self.signal_correlations),
        }

    def save(self):
        self._save()


pattern_analyzer = PatternAnalyzer()
