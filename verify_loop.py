import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable
from dex_client import DexScreenerClient
from config import config
from signal_filter import SignalFilter
from learner import load_data, save_data

logger = logging.getLogger("verify_loop")

class VerifyLoop:
    def __init__(self, dex: DexScreenerClient, filter_engine: SignalFilter, send_msg_func: Optional[Callable] = None):
        self.dex = dex
        self.filter_engine = filter_engine
        self.send_msg = send_msg_func
        self.pending_verifications: list = []
        self.completed: list = []

    def schedule_verification(self, address: str, symbol: str, launch_time: float, signal_price: float, social_score: float = 0.0, signal_score: float = 0.0):
        verification = {
            "address": address,
            "symbol": symbol,
            "launch_time": launch_time,
            "signal_price": signal_price,
            "signal_time": datetime.now(timezone.utc).timestamp(),
            "social_score": social_score,
            "signal_score": signal_score,
            "checks": [],
        }
        self.pending_verifications.append(verification)
        logger.info(f"⏰ Verification scheduled: {symbol} (T+15/30/60)")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_verification(verification))
        except RuntimeError:
            logger.debug(f"No event loop for {symbol}; verification queued but not auto-run")

    async def _run_verification(self, v: dict):
        address = v["address"]
        symbol = v["symbol"]
        launch_time = v["launch_time"]
        signal_price = v["signal_price"]

        checkpoints = [
            ("15m", 15 * 60),
            ("30m", 30 * 60),
            ("60m", 60 * 60),
        ]

        max_launch_mult = 0.0
        max_signal_mult = 0.0

        for label, delay in checkpoints:
            await asyncio.sleep(delay if label == "15m" else delay - (15 * 60 if label == "30m" else 30 * 60))

            try:
                pair = await self.dex.fetch_pair_data(address)
                if not pair:
                    continue
                current_price = float(pair.get("priceUsd", 0) or 0)
                if current_price <= 0 or signal_price <= 0:
                    continue

                if launch_time > 0:
                    try:
                        launch_data = await self.dex.fetch_pair_data(address)
                    except Exception:
                        launch_data = pair
                    launch_pair_created = launch_data.get("pairCreatedAt") if launch_data else None
                    if launch_pair_created:
                        try:
                            launch_estimate_price = current_price / (1 + (float(pair.get("priceChange", {}).get("h1", 0)) / 100))
                        except Exception:
                            launch_estimate_price = signal_price
                    else:
                        launch_estimate_price = signal_price
                else:
                    launch_estimate_price = signal_price

                signal_mult = current_price / signal_price
                launch_mult = current_price / launch_estimate_price if launch_estimate_price > 0 else signal_mult
                max_launch_mult = max(max_launch_mult, launch_mult)
                max_signal_mult = max(max_signal_mult, signal_mult)

                v["checks"].append({
                    "label": label,
                    "time": datetime.now(timezone.utc).isoformat(),
                    "current_price": current_price,
                    "signal_mult": round(signal_mult, 2),
                    "launch_mult": round(launch_mult, 2),
                })

                if self.send_msg:
                    emoji = "✅" if launch_mult >= 3 else "⏳" if launch_mult >= 1.5 else "⚠️"
                    await self.send_msg(
                        f"{emoji} <b>Verify {label} — ${symbol}</b>\n"
                        f"📈 From launch: <b>{launch_mult:.2f}x</b>\n"
                        f"📊 From signal: <b>{signal_mult:.2f}x</b>"
                    )
            except Exception as e:
                logger.error(f"Verify {label} error for {symbol}: {e}")

        v["final_multiplier"] = round(max_launch_mult, 2)
        v["final_signal_multiplier"] = round(max_signal_mult, 2)
        v["completed_at"] = datetime.now(timezone.utc).isoformat()

        if max_launch_mult >= 5.0:
            verdict = "STRONG_PUMP"
            emoji = "🎉"
        elif max_launch_mult >= 3.0:
            verdict = "PUMP"
            emoji = "✅"
        else:
            verdict = "DUMP"
            emoji = "❌"

        v["verdict"] = verdict
        self.completed.append(v)
        if v in self.pending_verifications:
            self.pending_verifications.remove(v)

        try:
            self.filter_engine.record_signal_result(
                address, symbol, {}, max_launch_mult, v.get("social_score", 0)
            )
        except Exception as e:
            logger.error(f"Filter record error: {e}")

        if self.send_msg:
            await self.send_msg(
                f"{emoji} <b>FINAL VERDICT — ${symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 From launch: <b>{max_launch_mult:.2f}x</b>\n"
                f"📈 From signal: <b>{max_signal_mult:.2f}x</b>\n"
                f"🏷️ Verdict: <b>{verdict}</b>\n"
                f"🎯 Target: 5x {'✅' if max_launch_mult >= 5 else '❌'}\n"
                f"🎯 Target: 3x {'✅' if max_launch_mult >= 3 else '❌'}"
            )

        logger.info(f"✅ Verify complete: {symbol} → {verdict} ({max_launch_mult:.2f}x)")

    def get_pending(self) -> list:
        return list(self.pending_verifications)

    def get_completed(self, limit: int = 20) -> list:
        return self.completed[-limit:]

    def get_stats(self) -> dict:
        completed = self.completed
        total = len(completed)
        pumps = sum(1 for v in completed if v.get("verdict") == "PUMP")
        strong = sum(1 for v in completed if v.get("verdict") == "STRONG_PUMP")
        dumps = sum(1 for v in completed if v.get("verdict") == "DUMP")
        return {
            "total_verified": total,
            "pumps": pumps,
            "strong_pumps": strong,
            "dumps": dumps,
            "win_rate": round((pumps + strong) / total * 100, 1) if total > 0 else 0,
            "strong_rate": round(strong / total * 100, 1) if total > 0 else 0,
        }
