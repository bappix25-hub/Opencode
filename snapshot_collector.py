"""
snapshot_collector.py — Collect price/volume snapshots for GMGN FEATURED tokens.
Builds 3m / 5m / 15m candles from DexScreener snapshots.
Auto-stops after 6 hours or when token dumps 70%+ from peak.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger("meme_bot.snapshot_collector")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_data.json")

# === CONFIG ===
SNAPSHOT_INTERVALS = [180, 300, 900]  # 3min, 5min, 15min in seconds
MAX_DURATION = 6 * 3600  # 6 hours
DUMP_THRESHOLD = 0.70  # 70% drop from peak = dump
DEX_TIMEOUT = 8


class CandleBuilder:
    """Build OHLCV candles from price snapshots."""

    def __init__(self, interval: int):
        self.interval = interval
        self.candles = []
        self._current = None
        self._bucket_start = 0

    def add_snapshot(self, ts: float, price: float, volume_5m: float = 0,
                     buys_5m: int = 0, sells_5m: int = 0):
        bucket = int(ts // self.interval) * self.interval
        if bucket != self._bucket_start:
            if self._current:
                self.candles.append(self._current)
            self._bucket_start = bucket
            self._current = {
                "time": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume_5m,
                "buys": buys_5m,
                "sells": sells_5m,
                "red": False,
            }
        elif self._current:
            self._current["high"] = max(self._current["high"], price)
            self._current["low"] = min(self._current["low"], price)
            self._current["close"] = price
            self._current["volume"] = max(self._current["volume"], volume_5m)
            self._current["buys"] = max(self._current["buys"], buys_5m)
            self._current["sells"] = max(self._current["sells"], sells_5m)

        if self._current:
            self._current["red"] = self._current["close"] < self._current["open"]

    def finalize(self):
        if self._current:
            self.candles.append(self._current)
            self._current = None
        return self.candles

    def trim(self, max_age: float):
        cutoff = time.time() - max_age
        self.candles = [c for c in self.candles if c["time"] >= cutoff]


class SnapshotSession:
    """Tracks one token from launch -> 6h or dump."""

    def __init__(self, ca: str, symbol: str, launch_ts: float,
                 initial_price: float, initial_mcap: float, initial_liq: float):
        self.ca = ca
        self.symbol = symbol
        self.launch_ts = launch_ts
        self.start_time = time.time()
        self.peak_price = initial_price
        self.low_price = initial_price
        self.initial_price = initial_price
        self.initial_mcap = initial_mcap
        self.initial_liq = initial_liq
        self.last_price = initial_price
        self.last_holders = 0
        self.last_top10 = 0
        self.status = "active"  # active | dumped | expired
        self.snapshots = []

        self.builders = {i: CandleBuilder(i) for i in SNAPSHOT_INTERVALS}

    def is_expired(self) -> bool:
        age = time.time() - self.start_time
        if age > MAX_DURATION:
            self.status = "expired"
            return True
        return False

    def is_dumped(self, current_price: float) -> bool:
        if self.peak_price > 0:
            drop = 1.0 - (current_price / self.peak_price)
            if drop >= DUMP_THRESHOLD:
                self.status = "dumped"
                return True
        return False

    def add_snapshot(self, ts: float, price: float, volume_5m: float,
                     volume_1h: float, liquidity: float, mcap: float,
                     buys_5m: int, sells_5m: int, holders: int = 0,
                     top10_pct: float = 0):
        snap = {
            "time": ts,
            "price": price,
            "volume_5m": volume_5m,
            "volume_1h": volume_1h,
            "liquidity": liquidity,
            "mcap": mcap,
            "buys_5m": buys_5m,
            "sells_5m": sells_5m,
            "bsr_5m": buys_5m / max(sells_5m, 1),
            "holders": holders,
            "top10_pct": top10_pct,
        }
        self.snapshots.append(snap)

        if price > self.peak_price:
            self.peak_price = price
        if price > 0 and (self.low_price == 0 or price < self.low_price):
            self.low_price = price
        self.last_price = price
        if holders:
            self.last_holders = holders
        if top10_pct:
            self.last_top10 = top10_pct

        # Feed candle builders
        for b in self.builders.values():
            b.add_snapshot(ts, price, volume_5m, buys_5m, sells_5m)

    def get_summary(self) -> dict:
        age = time.time() - self.start_time
        mult = self.last_price / self.initial_price if self.initial_price > 0 else 0
        return {
            "ca": self.ca,
            "symbol": self.symbol,
            "age_hours": round(age / 3600, 2),
            "status": self.status,
            "initial_price": self.initial_price,
            "peak_price": self.peak_price,
            "last_price": self.last_price,
            "multiplier": round(mult, 2),
            "peak_multiplier": round(self.peak_price / self.initial_price, 2) if self.initial_price > 0 else 0,
            "initial_mcap": self.initial_mcap,
            "initial_liq": self.initial_liq,
            "last_holders": self.last_holders,
            "last_top10": self.last_top10,
            "snapshot_count": len(self.snapshots),
            "candles_3m": len(self.builders[180].candles),
            "candles_5m": len(self.builders[300].candles),
            "candles_15m": len(self.builders[900].candles),
        }


class SnapshotCollector:
    """Manages multiple token snapshot sessions."""

    def __init__(self, dex_client, birdeye_client=None):
        self.dex = dex_client
        self.birdeye = birdeye_client
        self.sessions = {}  # ca -> SnapshotSession
        self.completed = []  # finished sessions
        self._load()

    def _load(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    data = json.load(f)
                self.completed = data.get("completed", [])
                logger.info(f"Loaded {len(self.completed)} completed snapshot sessions")
        except Exception as e:
            logger.debug(f"Load error: {e}")

    def _save(self):
        try:
            data = {
                "completed": self.completed[-200:],
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Save error: {e}")

    def start_tracking(self, ca: str, symbol: str, launch_ts: float,
                       initial_price: float, initial_mcap: float,
                       initial_liq: float):
        """Start a new snapshot session for a GMGN FEATURED token."""
        if ca in self.sessions:
            return
        if any(s.get("ca") == ca for s in self.completed[-50:]):
            return

        self.sessions[ca] = SnapshotSession(
            ca, symbol, launch_ts, initial_price, initial_mcap, initial_liq
        )
        logger.info(
            f"📸 Snapshot started: {symbol} ({ca[:8]}...) | "
            f"price=${initial_price:.8f} mcap=${initial_mcap:.0f} liq=${initial_liq:.0f}"
        )

    async def take_snapshot(self, ca: str):
        """Take a DexScreener snapshot for one token."""
        session = self.sessions.get(ca)
        if not session or session.status != "active":
            return

        try:
            pair = await asyncio.wait_for(
                self.dex.fetch_pair_data(ca), timeout=DEX_TIMEOUT
            )
            if not pair:
                return

            price = float(pair.get("priceUsd", 0) or 0)
            if price <= 0:
                return

            mcap = float(pair.get("fdv", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            txns = pair.get("txns") or {}
            vol = pair.get("volume") or {}

            volume_5m = float(vol.get("m5", 0) or 0)
            volume_1h = float(vol.get("h1", 0) or 0)
            buys_5m = int((txns.get("m5") or {}).get("buys", 0) or 0)
            sells_5m = int((txns.get("m5") or {}).get("sells", 0) or 0)
            holders = 0
            top10_pct = 0

            # Try Birdeye for holder data
            if self.birdeye:
                try:
                    overview = await asyncio.wait_for(
                        self.birdeye.get_overview(ca), timeout=8
                    )
                    if overview:
                        holders = overview.get("holders", 0) or 0
                        top10_pct = overview.get("top10_pct", 0) or 0
                except Exception:
                    pass

            now = time.time()
            session.add_snapshot(
                ts=now, price=price,
                volume_5m=volume_5m, volume_1h=volume_1h,
                liquidity=liquidity, mcap=mcap,
                buys_5m=buys_5m, sells_5m=sells_5m,
                holders=holders, top10_pct=top10_pct,
            )

            # Check dump
            if session.is_dumped(price):
                logger.info(
                    f"💀 Snapshot ended (dump): {session.symbol} | "
                    f"peak=${session.peak_price:.8f}→${price:.8f} "
                    f"({(1-price/session.peak_price)*100:.0f}% drop)"
                )
                self._complete_session(ca)
                return

            # Check expiry
            if session.is_expired():
                logger.info(
                    f"⏰ Snapshot ended (6h): {session.symbol} | "
                    f"{session.peak_price/session.initial_price:.1f}x peak"
                )
                self._complete_session(ca)
                return

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"Snapshot error {ca[:8]}: {e}")

    def _complete_session(self, ca: str):
        session = self.sessions.pop(ca, None)
        if session:
            self.completed.append(session.get_summary())
            self._save()

    async def scan_loop(self):
        """Snapshot all active tokens (called externally every ~60s)."""
        if not self.sessions:
            if self._last_session_count > 0:
                logger.info(f"📸 Snapshot scan: 0 active sessions")
            self._last_session_count = 0
            return

        for ca in list(self.sessions.keys()):
            await self.take_snapshot(ca)
            await asyncio.sleep(2)

        logger.info(
            f"📸 Snapshot scan: {len(self.sessions)} active, "
            f"{len(self.completed)} completed"
        )
        self._last_session_count = len(self.sessions)

    def get_session_stats(self) -> dict:
        return {
            "active": len(self.sessions),
            "completed": len(self.completed),
            "tokens": [
                {
                    "symbol": s.symbol,
                    "age_hours": round((time.time() - s.start_time) / 3600, 1),
                    "snapshots": len(s.snapshots),
                    "peak": f"{s.peak_price/s.initial_price:.1f}x" if s.initial_price > 0 else "?",
                    "price": s.last_price,
                }
                for s in self.sessions.values()
            ],
        }
