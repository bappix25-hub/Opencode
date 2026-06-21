import json
import os
import logging
import time
from typing import Dict, List, Tuple

logger = logging.getLogger("weight_optimizer")

OPTIMIZER_FILE = os.path.join(os.path.dirname(__file__), "data", "weight_optimization.json")


class WeightOptimizer:
    def __init__(self, learning_rate: float = 0.1, min_samples: int = 10):
        self.learning_rate = learning_rate
        self.min_samples = min_samples
        self.optimization_history: List[dict] = []
        self.current_weights: Dict[str, float] = {}
        self.current_min_quality: Dict[str, float] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(OPTIMIZER_FILE):
                with open(OPTIMIZER_FILE, "r") as f:
                    data = json.load(f)
                    self.optimization_history = data.get("history", [])
                    self.current_weights = data.get("current_weights", {})
                    self.current_min_quality = data.get("current_min_quality", {})
                logger.info(f"Weight optimizer loaded: {len(self.optimization_history)} optimization runs")
        except Exception as e:
            logger.error(f"Error loading weight optimizer: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(OPTIMIZER_FILE), exist_ok=True)
            data = {
                "history": self.optimization_history[-100:],
                "current_weights": self.current_weights,
                "current_min_quality": self.current_min_quality,
                "saved_at": time.time(),
            }
            with open(OPTIMIZER_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving weight optimizer: {e}")

    def optimize_weights(self, backtest_engine, current_weights: Dict[str, float]) -> Dict[str, float]:
        new_weights = current_weights.copy()
        changes = []

        for signal, stats in backtest_engine.signal_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < self.min_samples:
                continue

            win_rate = stats["wins"] / total
            avg_pnl = stats["total_pnl"] / total

            if signal not in current_weights:
                continue

            old_weight = current_weights[signal]

            if win_rate > 0.6 and avg_pnl > 10:
                target_weight = min(old_weight * 1.3, 0.5)
                new_weight = old_weight + (target_weight - old_weight) * self.learning_rate
                changes.append((signal, old_weight, new_weight, "increase"))
            elif win_rate < 0.4 or avg_pnl < -10:
                target_weight = max(old_weight * 0.7, 0.05)
                new_weight = old_weight + (target_weight - old_weight) * self.learning_rate
                changes.append((signal, old_weight, new_weight, "decrease"))
            else:
                continue

            new_weights[signal] = round(new_weight, 4)

        if changes:
            self.optimization_history.append({
                "timestamp": time.time(),
                "changes": [(s, o, n, r) for s, o, n, r in changes],
                "total_signals": len(backtest_engine.signal_stats),
            })

        self.current_weights = new_weights
        self._save()

        if changes:
            logger.info(f"Weight optimization: {len(changes)} adjustments")
            for signal, old, new, reason in changes[:5]:
                logger.info(f"  {signal}: {old:.3f} -> {new:.3f} ({reason})")

        return new_weights

    def optimize_min_quality(self, backtest_engine, current_min: Dict[str, float]) -> Dict[str, float]:
        new_min = current_min.copy()

        if len(backtest_engine.results) < 50:
            return new_min

        winning_liqs = [r["liquidity"] for r in backtest_engine.results if r["pnl_pct"] > 0 and r["liquidity"] > 0]
        losing_liqs = [r["liquidity"] for r in backtest_engine.results if r["pnl_pct"] < -10 and r["liquidity"] > 0]

        if winning_liqs and losing_liqs:
            avg_win_liq = sum(winning_liqs) / len(winning_liqs)
            avg_lose_liq = sum(losing_liqs) / len(losing_liqs)

            if avg_lose_liq > 0 and avg_win_liq / avg_lose_liq > 1.5:
                new_min_liq = avg_win_liq * 0.5
                old_liq = current_min.get("min_liquidity", 5000)
                new_min["min_liquidity"] = old_liq + (new_min_liq - old_liq) * self.learning_rate
                logger.info(f"Min liquidity adjusted: ${old_liq:.0f} -> ${new_min['min_liquidity']:.0f}")

        winning_fdvs = [r["fdv"] for r in backtest_engine.results if r["pnl_pct"] > 0 and r["fdv"] > 0]
        losing_fdvs = [r["fdv"] for r in backtest_engine.results if r["pnl_pct"] < -10 and r["fdv"] > 0]

        if winning_fdvs and losing_fdvs:
            avg_win_fdv = sum(winning_fdvs) / len(winning_fdvs)
            avg_lose_fdv = sum(losing_fdvs) / len(losing_fdvs)

            if avg_lose_fdv > 0 and avg_win_fdv / avg_lose_fdv > 1.5:
                new_min_fdv = avg_win_fdv * 0.5
                old_fdv = current_min.get("min_fdv", 3000)
                new_min["min_fdv"] = old_fdv + (new_min_fdv - old_fdv) * self.learning_rate
                logger.info(f"Min FDV adjusted: ${old_fdv:.0f} -> ${new_min['min_fdv']:.0f}")

        self.current_min_quality = new_min
        self._save()
        return new_min

    def calculate_expected_value(self, signal_combo: List[str], backtest_engine) -> float:
        if not signal_combo:
            return 0

        combo_key = "+".join(sorted(signal_combo))
        combo_stats = backtest_engine.combo_stats.get(combo_key)

        if combo_stats and combo_stats["count"] >= self.min_samples:
            win_rate = combo_stats["wins"] / combo_stats["count"]
            avg_pnl = combo_stats["total_pnl"] / combo_stats["count"]
            return win_rate * avg_pnl - (1 - win_rate) * abs(avg_pnl)

        individual_evs = []
        for signal in signal_combo:
            stats = backtest_engine.signal_stats.get(signal)
            if stats and stats["count"] >= self.min_samples:
                win_rate = stats["wins"] / stats["count"]
                avg_pnl = stats["total_pnl"] / stats["count"]
                ev = win_rate * avg_pnl - (1 - win_rate) * abs(avg_pnl)
                individual_evs.append(ev)

        if individual_evs:
            return sum(individual_evs) / len(individual_evs)

        return 0

    def apply_damping(self, old_weight: float, new_weight: float, momentum: float = 0.7) -> float:
        return old_weight * momentum + new_weight * (1 - momentum)

    def get_optimization_report(self) -> dict:
        if not self.optimization_history:
            return {"total_runs": 0, "latest": None}

        latest = self.optimization_history[-1]
        total_increases = sum(1 for _, _, _, r in latest.get("changes", []) if r == "increase")
        total_decreases = sum(1 for _, _, _, r in latest.get("changes", []) if r == "decrease")

        return {
            "total_runs": len(self.optimization_history),
            "latest_timestamp": latest.get("timestamp", 0),
            "latest_changes": len(latest.get("changes", [])),
            "increases": total_increases,
            "decreases": total_decreases,
            "current_weights": self.current_weights,
            "current_min_quality": self.current_min_quality,
        }

    def save(self):
        self._save()


weight_optimizer = WeightOptimizer()
