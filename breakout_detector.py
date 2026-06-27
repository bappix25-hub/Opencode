"""
breakout_detector.py — Research-only breakout pattern detection.

Monitors tokens and detects breakout patterns based on learned rules:
- Minimum age: 12 minutes
- Top 10 holders: max 40%
- 3-minute candles: minimum 3 red candles (consolidation)
- Price change: 30%+ from consolidation low = confirmed breakout
- Target: 3x+ pump

No buys are executed — only alerts and data logging.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger("meme_bot.breakout_detector")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "breakout_data.json")

# === CONFIGURATION ===
MIN_AGE_MINUTES = 12        # Token must be at least 12 min old
MAX_TOP10_PCT = 40           # Top 10 holders max 40%
MIN_RED_CANDLES = 3          # Minimum 3 red candles on 3m timeframe
BREAKOUT_PCT = 30            # 30%+ price change = breakout confirmed
TARGET_PUMP_X = 3.0          # Target 3x pump
CANDLE_INTERVAL = "3m"       # 3-minute candles
PRICE_CHECK_INTERVAL = 30    # Check price every 30 seconds
MAX_MONITORED_TOKENS = 200   # Max tokens to track simultaneously


class BreakoutDetector:
    """Detects breakout patterns in real-time. Research only — no trades."""

    def __init__(self, dex_client, birdeye_client=None):
        self.dex = dex_client
        self.birdeye = birdeye_client
        self.monitored = {}  # ca -> token data
        self.price_history = {}  # ca -> list of (timestamp, price)
        self.candles = {}  # ca -> list of {open, high, low, close, time}
        self.breakouts = []  # confirmed breakouts
        self._load_data()

    def _load_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    data = json.load(f)
                self.breakouts = data.get("breakouts", [])
                logger.info(f"Loaded {len(self.breakouts)} historical breakouts")
        except Exception as e:
            logger.debug(f"Load error: {e}")

    def _save_data(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump({
                    "breakouts": self.breakouts[-500:],  # Keep last 500
                    "updated": datetime.now(timezone.utc).isoformat()
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"Save error: {e}")

    def add_token(self, token: dict):
        """Add a token to monitor. Called from collector for every new token."""
        ca = token.get("ca", "")
        if not ca or ca in self.monitored:
            return

        # Apply entry filters
        top10 = token.get("top10_pct", 0)
        if top10 > MAX_TOP10_PCT:
            logger.debug(f"SKIP {token.get('symbol','?')}: top10={top10:.0f}% > {MAX_TOP10_PCT}%")
            return

        holders = token.get("holders", 0)
        if holders > 0 and holders <= 5:
            logger.debug(f"SKIP {token.get('symbol','?')}: holders={holders} <= 5")
            return

        pair_created = token.get("pair_created", 0)
        if pair_created <= 0:
            logger.debug(f"SKIP {token.get('symbol','?')}: no pair_created")
            return

        self.monitored[ca] = {
            "symbol": token.get("symbol", "?"),
            "ca": ca,
            "pair_created": pair_created,
            "top10_pct": top10,
            "holders": holders,
            "initial_price": 0,
            "initial_mcp": token.get("mcp", 0),
            "liq_usd": token.get("liq_usd", 0),
            "added_at": time.time(),
            "status": "watching",  # watching -> consolidation -> breakout -> target
            "consolidation_low": 0,
            "red_candle_count": 0,
            "breakout_price": 0,
        }

        # Trim if too many
        if len(self.monitored) > MAX_MONITORED_TOKENS:
            oldest = min(self.monitored.keys(), key=lambda k: self.monitored[k]["added_at"])
            del self.monitored[oldest]

        logger.info(f"📡 Monitoring {token.get('symbol','?')} ({ca[:8]}...) | top10={top10:.0f}% holders={holders} | {len(self.monitored)} total")

    async def check_token(self, ca: str) -> dict:
        """Check a single token for breakout conditions. Returns status dict."""
        if ca not in self.monitored:
            return {"status": "not_monitored"}

        token = self.monitored[ca]
        now = time.time()

        # Calculate age
        age_minutes = (now * 1000 - token["pair_created"]) / 60000
        if age_minutes < MIN_AGE_MINUTES:
            return {"status": "too_young", "age_minutes": round(age_minutes, 1)}

        # Get current price
        current_price = await self._get_price(ca)
        if not current_price or current_price <= 0:
            return {"status": "no_price"}

        # Record initial price if first check
        if token["initial_price"] <= 0:
            token["initial_price"] = current_price

        # Track price history
        if ca not in self.price_history:
            self.price_history[ca] = []
        self.price_history[ca].append((now, current_price))

        # Keep last 30 minutes of data
        cutoff = now - 1800
        self.price_history[ca] = [(t, p) for t, p in self.price_history[ca] if t > cutoff]

        # Build candles from price history
        self._build_candles(ca)

        # Check consolidation (3+ red candles on 3m)
        candle_status = self._check_consolidation(ca)

        # Check breakout (30%+ from consolidation low)
        if candle_status["status"] == "consolidation":
            consolidation_low = candle_status["low"]
            if consolidation_low > 0:
                pct_change = ((current_price - consolidation_low) / consolidation_low) * 100
                if pct_change >= BREAKOUT_PCT:
                    # BREAKOUT CONFIRMED
                    multiplier = current_price / token["initial_price"] if token["initial_price"] > 0 else 0
                    return {
                        "status": "breakout",
                        "symbol": token["symbol"],
                        "price": current_price,
                        "consolidation_low": consolidation_low,
                        "pct_change": round(pct_change, 1),
                        "multiplier": round(multiplier, 2),
                        "age_minutes": round(age_minutes, 1),
                        "top10_pct": token["top10_pct"],
                        "holders": token["holders"],
                        "target_3x": multiplier >= TARGET_PUMP_X,
                    }

            return {
                "status": "consolidation",
                "red_candles": candle_status["count"],
                "low": candle_status["low"],
            }

        return {
            "status": "watching",
            "age_minutes": round(age_minutes, 1),
            "price": current_price,
            "red_candles": candle_status.get("count", 0),
        }

    async def _get_price(self, ca: str) -> float:
        """Get current price from DexScreener."""
        try:
            pair = await asyncio.wait_for(self.dex.fetch_pair_data(ca), timeout=8)
            if pair:
                return float(pair.get("priceUsd", 0) or 0)
        except Exception:
            pass
        return 0

    def _build_candles(self, ca: str):
        """Build 3-minute candles from price history."""
        history = self.price_history.get(ca, [])
        if len(history) < 2:
            return

        candles = []
        candle_start = None
        candle_prices = []

        for ts, price in history:
            candle_minute = int(ts // 180) * 180  # 3-minute buckets

            if candle_start is None:
                candle_start = candle_minute
                candle_prices = [price]
            elif candle_minute == candle_start:
                candle_prices.append(price)
            else:
                # Close candle
                candles.append({
                    "open": candle_prices[0],
                    "high": max(candle_prices),
                    "low": min(candle_prices),
                    "close": candle_prices[-1],
                    "time": candle_start,
                    "red": candle_prices[-1] < candle_prices[0],  # Close < Open = red
                })
                candle_start = candle_minute
                candle_prices = [price]

        # Close last candle
        if candle_prices:
            candles.append({
                "open": candle_prices[0],
                "high": max(candle_prices),
                "low": min(candle_prices),
                "close": candle_prices[-1],
                "time": candle_start,
                "red": candle_prices[-1] < candle_prices[0],
            })

        self.candles[ca] = candles[-20:]  # Keep last 20 candles

    def _check_consolidation(self, ca: str) -> dict:
        """Check for consolidation pattern: 3+ consecutive red candles."""
        candles = self.candles.get(ca, [])
        if len(candles) < MIN_RED_CANDLES:
            return {"status": "insufficient_data", "count": 0, "low": 0}

        # Check last N candles for consecutive reds
        red_count = 0
        lowest_close = float("inf")

        for candle in reversed(candles):
            if candle["red"]:
                red_count += 1
                lowest_close = min(lowest_close, candle["close"])
            else:
                break  # Streak broken

        if red_count >= MIN_RED_CANDLES:
            return {"status": "consolidation", "count": red_count, "low": lowest_close}

        return {"status": "no_consolidation", "count": red_count, "low": lowest_close if lowest_close != float("inf") else 0}

    def record_breakout(self, breakout: dict):
        """Record a confirmed breakout for analysis."""
        breakout["detected_at"] = datetime.now(timezone.utc).isoformat()
        self.breakouts.append(breakout)
        self._save_data()

        symbol = breakout.get("symbol", "?")
        mult = breakout.get("multiplier", 0)
        pct = breakout.get("pct_change", 0)
        logger.info(
            f"🎯 BREAKOUT: {symbol} | {pct:+.1f}% from consolidation | "
            f"{mult:.1f}x from launch | age={breakout.get('age_minutes', 0)}min"
        )

    async def scan_all(self):
        """Check all monitored tokens for breakout conditions."""
        if not self.monitored:
            return

        results = []
        for ca in list(self.monitored.keys()):
            try:
                result = await self.check_token(ca)
                result["ca"] = ca
                results.append(result)

                if result["status"] == "breakout":
                    self.record_breakout(result)

            except Exception as e:
                logger.debug(f"Check error for {ca[:8]}: {e}")

        return results

    def get_stats(self) -> dict:
        """Get detector statistics."""
        return {
            "monitored": len(self.monitored),
            "breakouts": len(self.breakouts),
            "price_tracked": len(self.price_history),
            "candles_built": sum(len(c) for c in self.candles.values()),
        }
