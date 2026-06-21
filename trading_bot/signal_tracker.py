import json
import os
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Optional

logger = logging.getLogger("signal_tracker")

SIGNALS_FILE = os.path.join(os.path.dirname(__file__), "data", "signals.json")


@dataclass
class Signal:
    symbol: str
    address: str
    score: float
    signals: list
    price_at_signal: float
    mcap_at_signal: float
    timestamp: float
    signal_type: str = "pump"

    entry_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    current_price: float = 0.0

    tp_hit: bool = False
    sl_hit: bool = False
    tp_price: float = 0.0
    sl_price: float = 0.0
    max_pnl_pct: float = 0.0
    final_pnl_pct: float = 0.0

    best_tp_pct: float = 0.0
    best_sl_pct: float = 0.0
    best_profit_pct: float = 0.0

    status: str = "active"
    closed_at: float = 0.0

    holders: int = 0
    liquidity: float = 0.0
    top_10_pct: float = 0.0
    bundler_pct: float = 0.0
    dump_reason: str = ""
    features_snapshot: dict = None

    def __post_init__(self):
        if self.features_snapshot is None:
            self.features_snapshot = {}


class SignalTracker:
    def __init__(self):
        self.signals: List[Signal] = []
        self._load_signals()

    def _load_signals(self):
        try:
            if os.path.exists(SIGNALS_FILE):
                with open(SIGNALS_FILE, "r") as f:
                    data = json.load(f)
                    self.signals = [Signal(**s) for s in data]
                logger.info(f"Loaded {len(self.signals)} signals")
        except Exception as e:
            logger.error(f"Error loading signals: {e}")
            self.signals = []

    def _save_signals(self):
        try:
            os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
            with open(SIGNALS_FILE, "w") as f:
                json.dump([asdict(s) for s in self.signals], f, indent=2)
        except Exception as e:
            logger.error(f"Error saving signals: {e}")

    def add_signal(self, symbol: str, address: str, score: float, signals: list,
                   price: float, mcap: float, holders: int = 0, liquidity: float = 0.0,
                   features_snapshot: dict = None) -> Signal:
        signal = Signal(
            symbol=symbol,
            address=address,
            score=score,
            signals=signals,
            price_at_signal=price,
            mcap_at_signal=mcap,
            timestamp=datetime.now(timezone.utc).timestamp(),
            entry_price=price,
            highest_price=price,
            lowest_price=price,
            current_price=price,
            tp_price=price * 1.5,
            sl_price=price * 0.75,
            holders=holders,
            liquidity=liquidity,
            features_snapshot=features_snapshot or {},
        )
        for s in self.signals:
            if s.address == address and s.status == "active":
                age_min = (signal.timestamp - s.timestamp) / 60
                if age_min < 30:
                    logger.debug(f"Skip duplicate signal: {symbol} (sent {age_min:.0f}m ago)")
                    return s

        for s in self.signals:
            if s.address == address:
                age_min = (signal.timestamp - s.timestamp) / 60
                if age_min < 30:
                    logger.debug(f"Skip duplicate signal: {symbol} (last signal {age_min:.0f}m ago)")
                    return s

        self.signals.append(signal)
        self._save_signals()
        logger.info(f"Signal added: {symbol} @ {price} | Holders: {holders} | Liq: ${liquidity:.0f}")
        return signal

    def update_price(self, address: str, current_price: float) -> Optional[Signal]:
        for s in self.signals:
            if s.address == address and s.status == "active":
                s.current_price = current_price
                if current_price > s.highest_price:
                    s.highest_price = current_price
                if current_price < s.lowest_price:
                    s.lowest_price = current_price

                s.max_pnl_pct = ((s.highest_price - s.entry_price) / s.entry_price * 100) if s.entry_price > 0 else 0
                s.final_pnl_pct = ((current_price - s.entry_price) / s.entry_price * 100) if s.entry_price > 0 else 0

                if not s.tp_hit and current_price >= s.tp_price:
                    s.tp_hit = True
                if not s.sl_hit and current_price <= s.sl_price:
                    s.sl_hit = True

                self._calculate_best_tp_sl(s)
                self._save_signals()
                return s
        return None

    def _calculate_best_tp_sl(self, signal: Signal):
        if signal.entry_price <= 0:
            return

        best_profit = 0
        best_tp = 0
        best_sl = 0

        for tp_pct in [10, 20, 30, 50, 75, 100, 150, 200, 300, 500]:
            for sl_pct in [-5, -10, -15, -20, -25, -30, -50]:
                tp_price = signal.entry_price * (1 + tp_pct / 100)
                sl_price = signal.entry_price * (1 + sl_pct / 100)

                if signal.highest_price >= tp_price:
                    profit = tp_pct
                elif signal.lowest_price <= sl_price:
                    profit = sl_pct
                else:
                    profit = signal.final_pnl_pct

                if profit > best_profit:
                    best_profit = profit
                    best_tp = tp_pct
                    best_sl = sl_pct

        signal.best_tp_pct = best_tp
        signal.best_sl_pct = best_sl
        signal.best_profit_pct = best_profit

    def close_signal(self, address: str, exit_price: float) -> Optional[Signal]:
        for s in self.signals:
            if s.address == address and s.status == "active":
                s.status = "closed"
                s.current_price = exit_price
                s.final_pnl_pct = ((exit_price - s.entry_price) / s.entry_price * 100) if s.entry_price > 0 else 0
                s.closed_at = datetime.now(timezone.utc).timestamp()
                self._save_signals()
                return s
        return None

    def get_active_signals(self) -> List[Signal]:
        return [s for s in self.signals if s.status == "active"]

    def get_closed_signals(self, limit: int = 20) -> List[Signal]:
        closed = [s for s in self.signals if s.status == "closed"]
        return closed[-limit:]

    def get_signal_stats(self) -> dict:
        closed = [s for s in self.signals if s.status == "closed"]
        if not closed:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_pnl": 0, "avg_best_profit": 0}

        wins = sum(1 for s in closed if s.final_pnl_pct >= 0)
        losses = len(closed) - wins
        avg_pnl = sum(s.final_pnl_pct for s in closed) / len(closed)

        return {
            "total": len(closed),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / len(closed) * 100) if closed else 0,
            "avg_pnl": avg_pnl,
            "avg_best_profit": sum(s.best_profit_pct for s in closed) / len(closed) if closed else 0,
        }

    def get_dump_patterns(self) -> dict:
        dumps = [s for s in self.signals if s.final_pnl_pct < -20]
        if not dumps:
            return {"patterns": [], "avoid_holders_max": 0, "avoid_liquidity_max": 0, "dump_count": 0}

        holder_counts = [s.holders for s in dumps if s.holders > 0]
        liquidities = sorted([s.liquidity for s in dumps if s.liquidity > 0])

        if liquidities:
            mid = len(liquidities) // 2
            median_liq = liquidities[mid] if len(liquidities) % 2 == 1 else (liquidities[mid-1] + liquidities[mid]) / 2
            avoid_liq = min(median_liq * 1.5, 5000)
        else:
            avoid_liq = 5000

        return {
            "patterns": [
                {"symbol": s.symbol, "holders": s.holders, "liquidity": s.liquidity, "pnl": s.final_pnl_pct}
                for s in dumps[:10]
            ],
            "avoid_holders_max": min(max(holder_counts) if holder_counts else 10, 20),
            "avoid_liquidity_max": avoid_liq,
            "dump_count": len(dumps),
        }

    def should_avoid(self, holders: int, liquidity: float) -> tuple:
        patterns = self.get_dump_patterns()
        if patterns["dump_count"] < 3:
            return False, ""

        if 0 < holders <= patterns["avoid_holders_max"]:
            return True, f"Low holders ({holders} < {patterns['avoid_holders_max']})"

        if liquidity > 0 and liquidity <= patterns["avoid_liquidity_max"]:
            return True, f"Low liquidity (${liquidity:.0f} < ${patterns['avoid_liquidity_max']:.0f})"

        return False, ""


signal_tracker = SignalTracker()
