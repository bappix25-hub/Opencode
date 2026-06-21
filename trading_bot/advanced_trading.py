import json
import os
import logging
import time
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger("advanced_trading")

TRADING_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "advanced_trading.json")


@dataclass
class TrailingStopConfig:
    activation_pct: float = 10.0
    callback_pct: float = 5.0
    min_profit_pct: float = 5.0
    max_distance_pct: float = 20.0


@dataclass
class PartialExitConfig:
    levels: List[dict] = None
    def __post_init__(self):
        if self.levels is None:
            self.levels = [
                {"pct": 25, "tp": 30},
                {"pct": 25, "tp": 60},
                {"pct": 25, "tp": 100},
                {"pct": 25, "tp": 200},
            ]


@dataclass
class DCAConfig:
    enabled: bool = True
    num_entries: int = 3
    entry_pcts: List[float] = None
    def __post_init__(self):
        if self.entry_pcts is None:
            self.entry_pcts = [0.0, -5.0, -10.0]


@dataclass
class KellyConfig:
    fraction: float = 0.25
    min_edge: float = 0.1
    max_position_pct: float = 5.0


@dataclass
class PositionState:
    symbol: str
    entry_price: float
    current_price: float
    size_sol: float
    entry_time: float
    highest_price: float
    lowest_price: float
    trailing_stop_active: bool = False
    trailing_stop_price: float = 0.0
    partial_exits_done: List[float] = None
    dca_entries: int = 0
    pnl_pct: float = 0.0
    max_pnl_pct: float = 0.0

    def __post_init__(self):
        if self.partial_exits_done is None:
            self.partial_exits_done = []


class AdvancedTradingEngine:
    def __init__(self):
        self.trailing_stop_config = TrailingStopConfig()
        self.partial_exit_config = PartialExitConfig()
        self.dca_config = DCAConfig()
        self.kelly_config = KellyConfig()
        self.positions: Dict[str, PositionState] = {}
        self.trade_history: List[dict] = []
        self._load()

    def _load(self):
        try:
            if os.path.exists(TRADING_DATA_FILE):
                with open(TRADING_DATA_FILE, "r") as f:
                    data = json.load(f)
                    for addr, pos_data in data.get("positions", {}).items():
                        self.positions[addr] = PositionState(**pos_data)
                    self.trade_history = data.get("trade_history", [])[-500:]
        except Exception as e:
            logger.error(f"Error loading advanced trading: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(TRADING_DATA_FILE), exist_ok=True)
            data = {
                "positions": {k: asdict(v) for k, v in self.positions.items()},
                "trade_history": self.trade_history[-500:],
                "saved_at": time.time(),
            }
            with open(TRADING_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving advanced trading: {e}")

    def update_trailing_stop(self, symbol: str, current_price: float) -> Optional[float]:
        pos = self.positions.get(symbol)
        if not pos:
            return None

        pos.current_price = current_price
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        pos.pnl_pct = pnl_pct
        pos.max_pnl_pct = max(pos.max_pnl_pct, pnl_pct)

        if current_price > pos.highest_price:
            pos.highest_price = current_price

        if current_price < pos.lowest_price:
            pos.lowest_price = current_price

        if not pos.trailing_stop_active and pnl_pct >= self.trailing_stop_config.activation_pct:
            pos.trailing_stop_active = True
            pos.trailing_stop_price = current_price * (1 - self.trailing_stop_config.callback_pct / 100)
            logger.info(f"Trailing stop ACTIVATED for {symbol} at ${current_price:.8f} (stop: ${pos.trailing_stop_price:.8f})")

        if pos.trailing_stop_active:
            new_stop = pos.highest_price * (1 - self.trailing_stop_config.callback_pct / 100)
            if new_stop > pos.trailing_stop_price:
                pos.trailing_stop_price = new_stop

            if current_price <= pos.trailing_stop_price:
                logger.info(f"Trailing stop HIT for {symbol} at ${current_price:.8f}")
                self._save()
                return current_price

        self._save()
        return None

    def calculate_partial_exits(self, symbol: str, current_price: float) -> List[dict]:
        pos = self.positions.get(symbol)
        if not pos:
            return []

        exits = []
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

        for level in self.partial_exit_config.levels:
            tp_pct = level["tp"]
            exit_pct = level["pct"]

            if pnl_pct >= tp_pct and exit_pct not in pos.partial_exits_done:
                exits.append({
                    "symbol": symbol,
                    "exit_pct": exit_pct,
                    "tp_pct": tp_pct,
                    "current_pnl_pct": pnl_pct,
                    "price": current_price,
                })
                pos.partial_exits_done.append(exit_pct)

        if exits:
            self._save()

        return exits

    def calculate_kelly_position(self, win_rate: float, avg_win: float, avg_loss: float, bankroll_sol: float) -> float:
        if avg_loss == 0 or win_rate <= 0:
            return 0

        b = avg_win / abs(avg_loss)
        kelly_fraction = (win_rate * b - (1 - win_rate)) / b

        if kelly_fraction < self.kelly_config.min_edge:
            return 0

        kelly_fraction *= self.kelly_config.fraction

        max_position = bankroll_sol * (self.kelly_config.max_position_pct / 100)
        position_sol = bankroll_sol * kelly_fraction

        return min(position_sol, max_position)

    def should_dca(self, symbol: str, current_price: float) -> bool:
        pos = self.positions.get(symbol)
        if not pos or not self.dca_config.enabled:
            return False

        if pos.dca_entries >= self.dca_config.num_entries - 1:
            return False

        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        next_entry_pct = self.dca_config.entry_pcts[pos.dca_entries + 1] if pos.dca_entries + 1 < len(self.dca_config.entry_pcts) else -999

        if pnl_pct <= next_entry_pct:
            pos.dca_entries += 1
            pos.size_sol *= 2
            logger.info(f"DCA entry #{pos.dca_entries} for {symbol} at ${current_price:.8f} (pnl: {pnl_pct:.1f}%)")
            self._save()
            return True

        return False

    def calculate_dynamic_sl(self, symbol: str, current_price: float, base_sl_pct: float) -> float:
        pos = self.positions.get(symbol)
        if not pos:
            return base_sl_pct

        if pos.max_pnl_pct > 20:
            return max(base_sl_pct * 0.5, -15)
        elif pos.max_pnl_pct > 10:
            return max(base_sl_pct * 0.75, -20)

        return base_sl_pct

    def get_position_summary(self, symbol: str) -> dict:
        pos = self.positions.get(symbol)
        if not pos:
            return {"status": "no_position"}

        return {
            "symbol": pos.symbol,
            "entry_price": pos.entry_price,
            "current_price": pos.current_price,
            "size_sol": pos.size_sol,
            "pnl_pct": pos.pnl_pct,
            "max_pnl_pct": pos.max_pnl_pct,
            "trailing_stop_active": pos.trailing_stop_active,
            "trailing_stop_price": pos.trailing_stop_price,
            "partial_exits_done": pos.partial_exits_done,
            "dca_entries": pos.dca_entries,
            "hold_time_minutes": (time.time() - pos.entry_time) / 60,
        }

    def save(self):
        self._save()


advanced_trading = AdvancedTradingEngine()
