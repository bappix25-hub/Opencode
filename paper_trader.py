import json
import logging
import os
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from config import config

logger = logging.getLogger("paper_trader")

PAPER_TRADE_FILE = os.environ.get("PAPER_TRADE_FILE", "./paper_trades.json")

@dataclass
class Position:
    address: str
    symbol: str
    name: str
    entry_price: float
    entry_time: float
    sol_amount: float
    token_amount: float
    tp_price: float
    sl_price: float
    ai_score: float = 0.0
    social_score: float = 0.0
    signal_score: float = 0.0
    status: str = "open"
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0

@dataclass
class PaperState:
    initial_sol: float = 0.1
    current_sol: float = 0.1
    positions: list = field(default_factory=list)
    closed_trades: list = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_sol: float = 0.0
    peak_sol: float = 0.1
    max_drawdown_pct: float = 0.0

class PaperTrader:
    def __init__(self):
        self.state = PaperState()
        self._lock = asyncio.Lock()
        self._sol_price_usd: float = 150.0
        self._load_state()

    def _load_state(self):
        try:
            if os.path.exists(PAPER_TRADE_FILE):
                with open(PAPER_TRADE_FILE, "r") as f:
                    data = json.load(f)
                self.state.initial_sol = data.get("initial_sol", 0.1)
                self.state.current_sol = data.get("current_sol", 0.1)
                self.state.total_trades = data.get("total_trades", 0)
                self.state.wins = data.get("wins", 0)
                self.state.losses = data.get("losses", 0)
                self.state.total_pnl_sol = data.get("total_pnl_sol", 0.0)
                self.state.peak_sol = data.get("peak_sol", 0.1)
                self.state.max_drawdown_pct = data.get("max_drawdown_pct", 0.0)
                self.state.positions = [
                    Position(**p) for p in data.get("positions", [])
                ]
                self.state.closed_trades = [
                    Position(**p) for p in data.get("closed_trades", [])
                ]
                logger.info(f"📄 Paper state loaded: {self.state.current_sol:.4f} SOL, {len(self.state.positions)} open")
        except Exception as e:
            logger.error(f"Paper state load error: {e}")

    def _save_state(self):
        try:
            data = {
                "initial_sol": self.state.initial_sol,
                "current_sol": self.state.current_sol,
                "total_trades": self.state.total_trades,
                "wins": self.state.wins,
                "losses": self.state.losses,
                "total_pnl_sol": self.state.total_pnl_sol,
                "peak_sol": self.state.peak_sol,
                "max_drawdown_pct": self.state.max_drawdown_pct,
                "positions": [asdict(p) for p in self.state.positions],
                "closed_trades": [asdict(p) for p in self.state.closed_trades[-100:]],
            }
            with open(PAPER_TRADE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Paper state save error: {e}")

    def _calculate_tp(self, ai_score: float, social_score: float, signal_score: float, age_seconds: float,
                     buy_velocity: float = 0, curve_fill_pct: float = 0) -> tuple:
        """Fixed TP/SL based on data: 93% of pumps die after ATH.
        TP +100% (2x), SL -50% (half) — take profit fast, cut losses fast."""
        return 100.0, -50.0

    async def buy(self, address: str, symbol: str, name: str,
                  price_usd: float, ai_score: float, social_score: float,
                  signal_score: float, age_seconds: float,
                  buy_velocity: float = 0, curve_fill_pct: float = 0) -> Optional[Position]:
        async with self._lock:
            for p in self.state.positions:
                if p.address == address and p.status == "open":
                    return None

            sol_amount = config.paper_trade_sol_per_buy
            if self.state.current_sol < sol_amount:
                logger.warning(f"⚠️ Insufficient SOL: {self.state.current_sol:.4f} < {sol_amount}")
                return None

            tp_pct, sl_pct = self._calculate_tp(ai_score, social_score, signal_score, age_seconds,
                                                buy_velocity, curve_fill_pct)
            tp_price = price_usd * (1 + tp_pct / 100)
            sl_price = price_usd * (1 + sl_pct / 100)

            token_amount = sol_amount / price_usd if price_usd > 0 else 0

            position = Position(
                address=address,
                symbol=symbol,
                name=name,
                entry_price=price_usd,
                entry_time=datetime.now(timezone.utc).timestamp(),
                sol_amount=sol_amount,
                token_amount=token_amount,
                tp_price=tp_price,
                sl_price=sl_price,
                ai_score=ai_score,
                social_score=social_score,
                signal_score=signal_score,
            )

            self.state.positions.append(position)
            self.state.current_sol -= sol_amount
            self._save_state()

            logger.info(
                f"🟢 PAPER BUY: {symbol} | {sol_amount:.4f} SOL @ ${price_usd:.8f} | "
                f"TP: ${tp_price:.8f} ({tp_pct:+.0f}%) | SL: ${sl_price:.8f} ({sl_pct:+.0f}%) | "
                f"vel={buy_velocity:.1f} curve={curve_fill_pct:.0f}%"
            )
            return position

    async def check_tp_sl(self, address: str, current_price: float) -> Optional[Position]:
        async with self._lock:
            for p in self.state.positions:
                if p.address == address and p.status == "open":
                    if current_price <= 0:
                        return None

                    if current_price >= p.tp_price:
                        return await self._close_position(p, current_price, "tp_hit")
                    elif current_price <= p.sl_price:
                        return await self._close_position(p, current_price, "sl_hit")
            return None

    async def force_close(self, address: str, current_price: float) -> Optional[Position]:
        async with self._lock:
            for p in self.state.positions:
                if p.address == address and p.status == "open":
                    return await self._close_position(p, current_price, "manual")
            return None

    async def _close_position(self, p: Position, current_price: float, reason: str) -> Position:
        p.exit_price = current_price
        p.exit_time = datetime.now(timezone.utc).timestamp()
        p.exit_reason = reason

        if p.entry_price > 0:
            price_mult = current_price / p.entry_price
            p.pnl_sol = p.sol_amount * (price_mult - 1)
            p.pnl_pct = (price_mult - 1) * 100
        else:
            p.pnl_sol = 0
            p.pnl_pct = 0

        self.state.current_sol += p.sol_amount + p.pnl_sol
        self.state.total_trades += 1
        self.state.total_pnl_sol += p.pnl_sol

        if p.pnl_sol >= 0:
            self.state.wins += 1
        else:
            self.state.losses += 1

        if self.state.current_sol > self.state.peak_sol:
            self.state.peak_sol = self.state.current_sol

        if self.state.peak_sol > 0:
            drawdown = (self.state.peak_sol - self.state.current_sol) / self.state.peak_sol * 100
            if drawdown > self.state.max_drawdown_pct:
                self.state.max_drawdown_pct = round(drawdown, 2)

        p.status = "closed"
        self.state.closed_trades.append(p)
        self.state.positions = [pos for pos in self.state.positions if pos.status == "open"]
        self._save_state()

        emoji = "✅" if p.pnl_sol >= 0 else "❌"
        reason_map = {
            "tp_hit": "🎯 TP Hit",
            "sl_hit": "🛑 SL Hit",
            "manual": "✋ Manual",
            "timeout": "⏰ 3h Timeout",
        }
        reason_text = reason_map.get(reason, reason)
        logger.info(
            f"{emoji} PAPER CLOSE: {p.symbol} | {reason_text} | "
            f"${p.entry_price:.8f} → ${current_price:.8f} | "
            f"PnL: {p.pnl_sol:+.4f} SOL ({p.pnl_pct:+.1f}%)"
        )
        return p

    async def timeout_close_all(self, dex_client) -> list:
        closed = []
        async with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            for p in list(self.state.positions):
                if p.status == "open" and (now - p.entry_time) >= 10800:
                    try:
                        pair = await dex_client.fetch_pair_data(p.address)
                        if pair:
                            price = float(pair.get("priceUsd", 0) or 0)
                            if price > 0:
                                result = await self._close_position(p, price, "timeout")
                                closed.append(result)
                    except Exception as e:
                        logger.debug(f"Timeout close error for {p.symbol}: {e}")
        return closed

    def get_balance(self) -> dict:
        total_position_value = 0
        for p in self.state.positions:
            if p.status == "open":
                total_position_value += p.sol_amount

        total_value = self.state.current_sol + total_position_value
        pnl = total_value - self.state.initial_sol
        pnl_pct = (pnl / self.state.initial_sol * 100) if self.state.initial_sol > 0 else 0
        win_rate = (self.state.wins / self.state.total_trades * 100) if self.state.total_trades > 0 else 0

        return {
            "initial_sol": self.state.initial_sol,
            "current_sol": self.state.current_sol,
            "positions_value": total_position_value,
            "total_value": total_value,
            "pnl_sol": pnl,
            "pnl_pct": pnl_pct,
            "total_trades": self.state.total_trades,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "win_rate": win_rate,
            "peak_sol": self.state.peak_sol,
            "max_drawdown_pct": self.state.max_drawdown_pct,
        }

    def get_open_positions(self) -> list:
        return [p for p in self.state.positions if p.status == "open"]

    def get_closed_trades(self, limit: int = 20) -> list:
        return self.state.closed_trades[-limit:]

    def format_balance(self) -> str:
        b = self.get_balance()
        emoji = "🟢" if b["pnl_sol"] >= 0 else "🔴"
        bar_len = min(int(abs(b["pnl_pct"]) / 10), 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)

        text = (
            f"💰 <b>Paper Trading ব্যালেন্স</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🏦 শুরু: <b>{b['initial_sol']:.4f} SOL</b>\n"
            f"💵 বর্তমান: <b>{b['current_sol']:.4f} SOL</b>\n"
            f"📦 পজিশন: <b>{b['positions_value']:.4f} SOL</b>\n"
            f"💎 মোট ভ্যালু: <b>{b['total_value']:.4f} SOL</b>\n\n"
            f"{emoji} প্রফিট/লস: <b>{b['pnl_sol']:+.4f} SOL ({b['pnl_pct']:+.1f}%)</b>\n"
            f"📊 {bar}\n\n"
            f"📈 ট্রেড: <b>{b['total_trades']}</b>\n"
            f"✅ জয়: <b>{b['wins']}</b> | ❌ হার: <b>{b['losses']}</b>\n"
            f"🏆 Win Rate: <b>{b['win_rate']:.1f}%</b>\n"
            f"📈 Peak: <b>{b['peak_sol']:.4f} SOL</b>\n"
            f"📉 Max Drawdown: <b>{b['max_drawdown_pct']:.1f}%</b>"
        )
        return text

    def format_positions(self) -> str:
        positions = self.get_open_positions()
        if not positions:
            return "📦 কোনো ওপেন পজিশন নেই।"

        text = f"📦 <b>ওপেন পজিশন ({len(positions)} টি)</b>\n━━━━━━━━━━━━━━━━\n"
        for i, p in enumerate(positions, 1):
            age_min = (datetime.now(timezone.utc).timestamp() - p.entry_time) / 60
            tp_pct = ((p.tp_price / p.entry_price) - 1) * 100 if p.entry_price > 0 else 0
            sl_pct = ((p.sl_price / p.entry_price) - 1) * 100 if p.entry_price > 0 else 0
            text += (
                f"{i}. <b>${p.symbol}</b> ({p.name})\n"
                f"   💰 Entry: <b>${p.entry_price:.8f}</b>\n"
                f"   🎯 TP: <b>${p.tp_price:.8f}</b> ({tp_pct:+.0f}%)\n"
                f"   🛑 SL: <b>${p.sl_price:.8f}</b> ({sl_pct:+.0f}%)\n"
                f"   💵 SOL: <b>{p.sol_amount:.4f}</b>\n"
                f"   ⏱️ Age: <b>{age_min:.0f}m</b>\n"
                f"   🧠 AI: {p.ai_score:.2f} | Social: {p.social_score:.2f}\n"
            )
        return text

    def format_trade_history(self, limit: int = 10) -> str:
        trades = self.get_closed_trades(limit)
        if not trades:
            return "📜 কোনো ট্রেড হয়নি এখনো।"

        text = f"📜 <b>ট্রেড হিস্ট্রি (শেষ {len(trades)} টি)</b>\n━━━━━━━━━━━━━━━━\n"
        for t in reversed(trades):
            emoji = "✅" if t.pnl_sol >= 0 else "❌"
            reason_map = {"tp_hit": "🎯TP", "sl_hit": "🛑SL", "manual": "✋", "timeout": "⏰"}
            reason = reason_map.get(t.exit_reason, t.exit_reason)
            text += (
                f"{emoji} <b>${t.symbol}</b> | {reason}\n"
                f"   📈 ${t.entry_price:.8f} → ${t.exit_price:.8f}\n"
                f"   💰 PnL: <b>{t.pnl_sol:+.4f} SOL ({t.pnl_pct:+.1f}%)</b>\n"
            )
        return text

paper_trader: Optional[PaperTrader] = None

def get_paper_trader() -> PaperTrader:
    global paper_trader
    if paper_trader is None:
        paper_trader = PaperTrader()
    return paper_trader
