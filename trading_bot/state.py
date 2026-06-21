import json
import os
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime, timezone
from config import config

logger = logging.getLogger("state")

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "trades.json")


@dataclass
class TradePosition:
    address: str
    symbol: str
    name: str
    entry_price: float
    entry_time: float
    sol_amount: float
    token_amount: float
    tp_price: float
    sl_price: float
    signal_score: float = 0.0
    entry_mcap: float = 0.0
    tp_mcap: float = 0.0
    sl_mcap: float = 0.0
    status: str = "open"
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class TradeStats:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_sol: float = 0.0
    peak_sol: float = 0.0
    max_drawdown_pct: float = 0.0


class TradeState:
    def __init__(self):
        self.positions: list = []
        self.closed_trades: list = []
        self.stats = TradeStats()
        self._load_state()
        self._cleanup_stale_positions()

    def was_recently_traded(self, address: str, cooldown_minutes: int = 60) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        for p in self.positions:
            if p.status == "open" and p.address == address:
                return True
        for p in self.closed_trades:
            if p.address == address and p.exit_time > 0:
                age_min = (now - p.exit_time) / 60
                if age_min < cooldown_minutes:
                    return True
        return False

    def _cleanup_stale_positions(self):
        now = datetime.now(timezone.utc).timestamp()
        still_open = []
        for pos in self.positions:
            if pos.status != "open":
                continue
            age_hours = (now - pos.entry_time) / 3600
            if age_hours > 4:
                logger.warning(
                    f"Stale position: {pos.symbol} (age: {age_hours:.1f}h) - auto closing"
                )
                pos.status = "closed"
                pos.exit_reason = "Stale (>4h)"
                pos.exit_time = now
                if pos.entry_price > 0:
                    pos.exit_price = pos.entry_price
                    pos.pnl_sol = 0
                    pos.pnl_pct = 0
                self.closed_trades.append(pos)
            else:
                still_open.append(pos)
        self.positions = still_open
        if len(self.positions) < len(still_open) + 1:
            self._save_state()

    def _load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)

                positions_data = data.get("positions", [])
                self.positions = [TradePosition(**p) for p in positions_data if p.get("status") == "open"]

                self.closed_trades = [TradePosition(**t) for t in data.get("closed_trades", [])]

                stats = data.get("stats", {})
                self.stats.total_trades = stats.get("total_trades", 0)
                self.stats.wins = stats.get("wins", 0)
                self.stats.losses = stats.get("losses", 0)
                self.stats.total_pnl_sol = stats.get("total_pnl_sol", 0.0)
                self.stats.peak_sol = stats.get("peak_sol", 0.0)
                self.stats.max_drawdown_pct = stats.get("max_drawdown_pct", 0.0)

                logger.info(f"State loaded: {len(self.positions)} positions, {self.stats.total_trades} trades, PnL: {self.stats.total_pnl_sol:+.4f} SOL")
        except Exception as e:
            logger.error(f"State load error: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            data = {
                "positions": [asdict(p) for p in self.positions],
                "closed_trades": [asdict(t) for t in self.closed_trades[-100:]],
                "stats": {
                    "total_trades": self.stats.total_trades,
                    "wins": self.stats.wins,
                    "losses": self.stats.losses,
                    "total_pnl_sol": self.stats.total_pnl_sol,
                    "peak_sol": self.stats.peak_sol,
                    "max_drawdown_pct": self.stats.max_drawdown_pct,
                },
            }
            with open(STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def has_open_position(self, address: str = None) -> bool:
        if address:
            return any(p.status == "open" and p.address == address for p in self.positions)
        return any(p.status == "open" for p in self.positions)

    def get_open_positions(self) -> list:
        return [p for p in self.positions if p.status == "open"]

    def get_position_count(self) -> int:
        return len([p for p in self.positions if p.status == "open"])

    def open_position(self, position: TradePosition):
        self.positions.append(position)
        self._save_state()
        logger.info(f"Position opened: {position.symbol} ({self.get_position_count()}/{config.max_positions})")

    def close_position(self, address: str, exit_price: float, reason: str) -> Optional[TradePosition]:
        for p in self.positions:
            if p.status == "open" and p.address == address:
                p.exit_price = exit_price
                p.exit_time = datetime.now(timezone.utc).timestamp()
                p.exit_reason = reason

                if p.entry_price > 0:
                    price_mult = exit_price / p.entry_price
                    p.pnl_sol = p.sol_amount * (price_mult - 1)
                    p.pnl_pct = (price_mult - 1) * 100

                p.status = "closed"
                self.closed_trades.append(p)

                self.stats.total_trades += 1
                self.stats.total_pnl_sol += p.pnl_sol

                if p.pnl_sol >= 0:
                    self.stats.wins += 1
                else:
                    self.stats.losses += 1

                total_value = self.stats.peak_sol + p.pnl_sol
                if total_value > self.stats.peak_sol:
                    self.stats.peak_sol = total_value

                if self.stats.peak_sol > 0:
                    drawdown = (self.stats.peak_sol - total_value) / self.stats.peak_sol * 100
                    if drawdown > self.stats.max_drawdown_pct:
                        self.stats.max_drawdown_pct = round(drawdown, 2)

                self.positions = [pp for pp in self.positions if pp.status == "open"]
                self._save_state()

                emoji = "✅" if p.pnl_sol >= 0 else "❌"
                logger.info(f"{emoji} Position closed: {p.symbol} | {reason} | PnL: {p.pnl_sol:+.4f} SOL ({p.pnl_pct:+.1f}%)")
                return p
        return None

    def close_all_positions(self, reason: str = "Manual close") -> list:
        closed = []
        for p in list(self.positions):
            if p.status == "open":
                result = self.close_position(p.address, p.entry_price, reason)
                if result:
                    closed.append(result)
        return closed

    def get_win_rate(self) -> float:
        if self.stats.total_trades == 0:
            return 0.0
        return (self.stats.wins / self.stats.total_trades) * 100

    def format_stats(self) -> str:
        wr = self.get_win_rate()
        positions = self.get_open_positions()
        pos_str = ""
        for p in positions:
            age_min = (datetime.now(timezone.utc).timestamp() - p.entry_time) / 60
            pos_str += f"\n  📍 {p.symbol} ({age_min:.0f}m) | Score: {p.signal_score:.2f}"
        if not pos_str:
            pos_str = "\n  None"
        return (
            f"📊 Trade Stats\n"
            f"Total: {self.stats.total_trades} | "
            f"W: {self.stats.wins} | L: {self.stats.losses}\n"
            f"Win Rate: {wr:.1f}%\n"
            f"PnL: {self.stats.total_pnl_sol:+.4f} SOL\n"
            f"Max DD: {self.stats.max_drawdown_pct:.1f}%\n"
            f"\n💼 Positions ({len(positions)}/{config.max_positions}):{pos_str}"
        )
