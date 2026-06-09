import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable
from dex_client import DexScreenerClient
from config import config
from signal_filter import SignalFilter
from learner import load_data, save_data, update_signal_ath

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
            ("30m", 30 * 60),
            ("1h", 60 * 60),
            ("2h", 120 * 60),
            ("3h", 180 * 60),
        ]

        max_launch_mult = 0.0
        max_signal_mult = 0.0
        ath_price = signal_price
        ath_mult = 1.0

        for label, delay in checkpoints:
            if label == "30m":
                await asyncio.sleep(delay)
            else:
                prev_delay = {"1h": 30 * 60, "2h": 60 * 60, "3h": 120 * 60}[label]
                await asyncio.sleep(delay - prev_delay)

            try:
                pair = await self.dex.fetch_pair_data(address)
                if not pair:
                    continue
                current_price = float(pair.get("priceUsd", 0) or 0)
                if current_price <= 0 or signal_price <= 0:
                    continue

                if current_price > ath_price:
                    ath_price = current_price
                    ath_mult = current_price / signal_price

                if launch_time > 0:
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
                    "ath_price": ath_price,
                    "ath_mult": round(ath_mult, 2),
                })

                if self.send_msg:
                    emoji = "✅" if launch_mult >= 3 else "⏳" if launch_mult >= 1.5 else "⚠️"
                    ath_text = f"\n📈 ATH: <b>{ath_mult:.2f}x</b>" if ath_mult > 1.5 else ""
                    await self.send_msg(
                        f"{emoji} <b>Verify {label} — ${symbol}</b>\n"
                        f"📈 From launch: <b>{launch_mult:.2f}x</b>\n"
                        f"📊 From signal: <b>{signal_mult:.2f}x</b>{ath_text}\n"
                        f"💰 Paper PnL: <b>{((current_price / signal_price) - 1) * 100:+.1f}%</b>"
                    )
            except Exception as e:
                logger.error(f"Verify {label} error for {symbol}: {e}")

        v["final_multiplier"] = round(max_launch_mult, 2)
        v["final_signal_multiplier"] = round(max_signal_mult, 2)
        v["ath_price"] = ath_price
        v["ath_multiplier"] = round(ath_mult, 2)
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

        if max_launch_mult < 1.5 and max_signal_mult < 1.2:
            try:
                self.filter_engine.add_to_blacklist(address, f"All dumps for {symbol} (launch {max_launch_mult:.2f}x)")
            except Exception:
                pass

        if self.send_msg:
            await self.send_msg(
                f"{emoji} <b>FINAL VERDICT — ${symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 From launch: <b>{max_launch_mult:.2f}x</b>\n"
                f"📈 From signal: <b>{max_signal_mult:.2f}x</b>\n"
                f"📈 ATH: <b>{ath_mult:.2f}x</b> (${ath_price:.8f})\n"
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
