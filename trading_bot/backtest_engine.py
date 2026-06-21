import json
import os
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger("backtest")

BACKTEST_FILE = os.path.join(os.path.dirname(__file__), "data", "backtest_results.json")

TP_LEVELS = [10, 20, 30, 50, 75, 100, 150, 200]
SL_LEVELS = [-5, -10, -15, -20, -25, -30, -50]


@dataclass
class BacktestResult:
    token_address: str
    symbol: str
    entry_time: float
    entry_price: float
    exit_price: float
    exit_time: float
    exit_reason: str
    pnl_pct: float
    max_pnl_pct: float
    best_tp: float
    best_sl: float
    signals: list
    features: dict
    liquidity: float
    fdv: float
    score: float


class BacktestEngine:
    def __init__(self):
        self.results: List[dict] = []
        self.signal_stats: Dict[str, dict] = {}
        self.combo_stats: Dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(BACKTEST_FILE):
                with open(BACKTEST_FILE, "r") as f:
                    data = json.load(f)
                    self.results = data.get("results", [])
                    self.signal_stats = data.get("signal_stats", {})
                    self.combo_stats = data.get("combo_stats", {})
                logger.info(f"Backtest results loaded: {len(self.results)} results")
        except Exception as e:
            logger.error(f"Error loading backtest results: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(BACKTEST_FILE), exist_ok=True)
            data = {
                "results": self.results[-2000:],
                "signal_stats": self.signal_stats,
                "combo_stats": self.combo_stats,
                "saved_at": time.time(),
            }
            with open(BACKTEST_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error saving backtest results: {e}")

    def simulate_entry(self, prices: List[List[float]], entry_idx: int,
                       tp_pct: float, sl_pct: float) -> Tuple[float, float, int, str]:
        if entry_idx >= len(prices):
            return 0, 0, entry_idx, "no_entry"

        entry_price = prices[entry_idx][1]
        if entry_price <= 0:
            return 0, 0, entry_idx, "invalid_entry"

        tp_price = entry_price * (1 + tp_pct / 100)
        sl_price = entry_price * (1 + sl_pct / 100)

        max_pnl = 0
        exit_idx = entry_idx
        exit_reason = "timeout"

        for i in range(entry_idx + 1, min(entry_idx + 200, len(prices))):
            price = prices[i][1]
            if price <= 0:
                continue

            pnl = (price - entry_price) / entry_price * 100
            max_pnl = max(max_pnl, pnl)

            if price >= tp_price:
                return pnl, max_pnl, i, "tp"
            if price <= sl_price:
                return pnl, max_pnl, i, "sl"

            exit_idx = i
            exit_reason = "timeout"

        final_price = prices[exit_idx][1] if exit_idx < len(prices) else entry_price
        final_pnl = (final_price - entry_price) / entry_price * 100
        return final_pnl, max_pnl, exit_idx, exit_reason

    def find_optimal_exit(self, prices: List[List[float]], entry_idx: int) -> Tuple[float, float, float, float]:
        best_pnl = -100
        best_tp = 50
        best_sl = -25

        for tp in TP_LEVELS:
            for sl in SL_LEVELS:
                pnl, _, _, _ = self.simulate_entry(prices, entry_idx, tp, sl)
                if pnl > best_pnl:
                    best_pnl = pnl
                    best_tp = tp
                    best_sl = sl

        return best_pnl, best_tp, best_sl, entry_idx

    def backtest_token(self, token_address: str, prices: List[List[float]],
                       signals: list, features: dict, metadata: dict = None) -> List[dict]:
        if len(prices) < 5:
            return []

        results = []
        symbol = metadata.get("symbol", "???") if metadata else "???"

        for i in range(len(prices) - 5):
            entry_time = prices[i][0]
            entry_price = prices[i][1]

            if entry_price <= 0:
                continue

            best_pnl, best_tp, best_sl, exit_idx = self.find_optimal_exit(prices, i)

            for tp, sl in [(50, -25), (30, -15), (100, -30), (20, -10)]:
                pnl, max_pnl, final_exit_idx, exit_reason = self.simulate_entry(prices, i, tp, sl)

                exit_price = prices[final_exit_idx][1] if final_exit_idx < len(prices) else entry_price
                exit_time = prices[final_exit_idx][0] if final_exit_idx < len(prices) else entry_time

                result = BacktestResult(
                    token_address=token_address,
                    symbol=symbol,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    exit_time=exit_time,
                    exit_reason=exit_reason,
                    pnl_pct=pnl,
                    max_pnl_pct=max_pnl,
                    best_tp=best_tp,
                    best_sl=best_sl,
                    signals=signals,
                    features=features,
                    liquidity=prices[i][3] if len(prices[i]) > 3 else 0,
                    fdv=prices[i][4] if len(prices[i]) > 4 else 0,
                    score=features.get("score", 0),
                )
                results.append(asdict(result))
                break

        return results

    def update_signal_stats(self, results: List[dict]):
        for result in results:
            is_win = result["pnl_pct"] > 0

            for signal in result.get("signals", []):
                if signal not in self.signal_stats:
                    self.signal_stats[signal] = {"wins": 0, "losses": 0, "total_pnl": 0, "count": 0}

                stats = self.signal_stats[signal]
                stats["count"] += 1
                stats["total_pnl"] += result["pnl_pct"]
                if is_win:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1

            combo_key = "+".join(sorted(result.get("signals", [])))
            if combo_key:
                if combo_key not in self.combo_stats:
                    self.combo_stats[combo_key] = {"wins": 0, "losses": 0, "total_pnl": 0, "count": 0}

                stats = self.combo_stats[combo_key]
                stats["count"] += 1
                stats["total_pnl"] += result["pnl_pct"]
                if is_win:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1

    def get_signal_win_rate(self, signal: str) -> float:
        if signal not in self.signal_stats:
            return 0
        stats = self.signal_stats[signal]
        total = stats["wins"] + stats["losses"]
        return stats["wins"] / total if total > 0 else 0

    def get_combo_win_rate(self, combo: str) -> float:
        if combo not in self.combo_stats:
            return 0
        stats = self.combo_stats[combo]
        total = stats["wins"] + stats["losses"]
        return stats["wins"] / total if total > 0 else 0

    def get_top_signals(self, min_count: int = 5) -> List[Tuple[str, float, int]]:
        results = []
        for signal, stats in self.signal_stats.items():
            total = stats["wins"] + stats["losses"]
            if total >= min_count:
                win_rate = stats["wins"] / total
                avg_pnl = stats["total_pnl"] / total
                results.append((signal, win_rate, total, avg_pnl))
        return sorted(results, key=lambda x: x[1], reverse=True)

    def get_top_combos(self, min_count: int = 3) -> List[Tuple[str, float, int]]:
        results = []
        for combo, stats in self.combo_stats.items():
            total = stats["wins"] + stats["losses"]
            if total >= min_count:
                win_rate = stats["wins"] / total
                avg_pnl = stats["total_pnl"] / total
                results.append((combo, win_rate, total, avg_pnl))
        return sorted(results, key=lambda x: x[1], reverse=True)

    def run_backtest_cycle(self, price_history, token_addresses: list = None) -> int:
        from price_history import price_history as ph

        addresses = token_addresses or list(ph.records.keys())
        all_results = []
        backtested = 0

        for addr in addresses:
            history = ph.get_history(addr, max_age_seconds=7*86400)
            if len(history) < 10:
                continue

            metadata = ph.metadata.get(addr, {})
            signals = []
            features = {
                "liquidity": history[-1][3] if history[-1][3] else 0,
                "fdv": history[-1][4] if history[-1][4] else 0,
            }

            results = self.backtest_token(addr, history, signals, features, metadata)
            all_results.extend(results)
            backtested += 1

        if all_results:
            self.update_signal_stats(all_results)
            self.results.extend(all_results[-500:])
            self._save()
            logger.info(f"Backtest cycle: {backtested} tokens, {len(all_results)} simulations")

        return backtested

    def get_accuracy_report(self) -> dict:
        total = len(self.results)
        if total == 0:
            return {"total": 0, "win_rate": 0, "avg_pnl": 0}

        wins = sum(1 for r in self.results if r["pnl_pct"] > 0)
        losses = total - wins
        avg_pnl = sum(r["pnl_pct"] for r in self.results) / total

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total * 100,
            "avg_pnl": avg_pnl,
            "top_signals": self.get_top_signals(3),
            "top_combos": self.get_top_combos(2),
        }

    def save(self):
        self._save()


backtest_engine = BacktestEngine()
