import json
import os
import logging
import time
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

logger = logging.getLogger("price_history")

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "price_history.json")


class PriceHistory:
    def __init__(self, max_tokens: int = 500, max_age_days: int = 7, max_points_per_token: int = 2000):
        self.records: Dict[str, List[List[float]]] = {}
        self.metadata: Dict[str, dict] = {}
        self.max_tokens = max_tokens
        self.max_age_days = max_age_days
        self.max_points_per_token = max_points_per_token
        self._load()

    def _load(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r") as f:
                    data = json.load(f)
                    self.records = data.get("records", {})
                    self.metadata = data.get("metadata", {})
                logger.info(f"Price history loaded: {len(self.records)} tokens")
        except Exception as e:
            logger.error(f"Error loading price history: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            data = {
                "records": self.records,
                "metadata": self.metadata,
                "saved_at": time.time(),
            }
            with open(DATA_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error saving price history: {e}")

    def record(self, token_address: str, price: float, volume_5m: float = 0,
               liquidity: float = 0, fdv: float = 0, timestamp: float = None):
        if price <= 0:
            return

        ts = timestamp or time.time()

        if token_address not in self.records:
            if len(self.records) >= self.max_tokens:
                self._evict_oldest()
            self.records[token_address] = []
            self.metadata[token_address] = {
                "first_seen": ts,
                "symbol": "",
                "coin_type": "",
            }

        point = [ts, price, volume_5m, liquidity, fdv]
        self.records[token_address].append(point)

        if len(self.records[token_address]) > self.max_points_per_token:
            self.records[token_address] = self.records[token_address][-self.max_points_per_token:]

    def update_metadata(self, token_address: str, **kwargs):
        if token_address in self.metadata:
            self.metadata[token_address].update(kwargs)

    def get_history(self, token_address: str, max_age_seconds: float = None) -> List[List[float]]:
        if token_address not in self.records:
            return []

        history = self.records[token_address]
        if max_age_seconds:
            cutoff = time.time() - max_age_seconds
            history = [p for p in history if p[0] >= cutoff]

        return history

    def get_price_changes(self, token_address: str) -> dict:
        history = self.records.get(token_address, [])
        if not history:
            return {"5m": 0, "15m": 0, "1h": 0, "change_from_first": 0}

        now = time.time()
        current_price = history[-1][1]

        changes = {}
        for label, seconds in [("5m", 300), ("15m", 900), ("1h", 3600)]:
            cutoff = now - seconds
            past_prices = [p for p in history if p[0] <= cutoff]
            if past_prices:
                past_price = past_prices[-1][1]
                changes[label] = ((current_price - past_price) / past_price * 100) if past_price > 0 else 0
            else:
                changes[label] = 0

        if history[0][1] > 0:
            changes["change_from_first"] = (current_price - history[0][1]) / history[0][1] * 100
        else:
            changes["change_from_first"] = 0

        return changes

    def get_volume_profile(self, token_address: str) -> dict:
        history = self.records.get(token_address, [])
        if not history:
            return {"avg_5m": 0, "max_5m": 0, "trend": 0}

        volumes = [p[2] for p in history if p[2] > 0]
        if not volumes:
            return {"avg_5m": 0, "max_5m": 0, "trend": 0}

        avg_vol = sum(volumes) / len(volumes)
        max_vol = max(volumes)

        if len(volumes) >= 10:
            first_half = volumes[:len(volumes)//2]
            second_half = volumes[len(volumes)//2:]
            avg_first = sum(first_half) / len(first_half) if first_half else 0
            avg_second = sum(second_half) / len(second_half) if second_half else 0
            trend = ((avg_second - avg_first) / avg_first * 100) if avg_first > 0 else 0
        else:
            trend = 0

        return {"avg_5m": avg_vol, "max_5m": max_vol, "trend": trend}

    def get_liquidity_profile(self, token_address: str) -> dict:
        history = self.records.get(token_address, [])
        if not history:
            return {"current": 0, "min": 0, "max": 0, "stability": 0}

        liquidities = [p[3] for p in history if p[3] > 0]
        if not liquidities:
            return {"current": 0, "min": 0, "max": 0, "stability": 0}

        current = liquidities[-1]
        min_liq = min(liquidities)
        max_liq = max(liquidities)

        if max_liq > 0:
            stability = 1 - (max_liq - min_liq) / max_liq
        else:
            stability = 0

        return {"current": current, "min": min_liq, "max": max_liq, "stability": stability}

    def get_token_count(self) -> int:
        return len(self.records)

    def get_total_data_points(self) -> int:
        return sum(len(v) for v in self.records.values())

    def _evict_oldest(self):
        if not self.records:
            return

        oldest_addr = min(self.records.keys(), key=lambda x: self.metadata.get(x, {}).get("first_seen", float("inf")))
        del self.records[oldest_addr]
        if oldest_addr in self.metadata:
            del self.metadata[oldest_addr]
        logger.debug(f"Evicted oldest token: {oldest_addr}")

    def cleanup(self, max_age_days: float = None):
        days = max_age_days or self.max_age_days
        cutoff = time.time() - (days * 86400)
        removed = 0

        for addr in list(self.records.keys()):
            history = self.records[addr]
            self.records[addr] = [p for p in history if p[0] >= cutoff]
            if not self.records[addr]:
                del self.records[addr]
                if addr in self.metadata:
                    del self.metadata[addr]
                removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old tokens from price history")

    def save(self):
        self._save()


price_history = PriceHistory()
