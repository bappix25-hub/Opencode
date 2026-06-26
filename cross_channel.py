"""
cross_channel.py — Track tokens across ALL 5 channels.

Flow:
1. Token appears in channel A → store
2. Same token in channel B → increment channel count
3. 2+ channels → cross-channel signal (higher confidence)
4. Channel reliability scoring (which channels produce winners)

Reduces DexScreener calls by using channel data first.
"""

import json
import os
import time
from datetime import datetime, timezone
from collections import defaultdict

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cross_channel_data.json")

# Channel IDs to names mapping
CHANNEL_NAMES = {
    -1002122751413: "New Pool Alert",
    -1002126036544: "LP Chat",
    -1002037135333: "New Token Bot",
    -1002064472392: "Listing Bot",
    -1002202241417: "GMGN Signals",
}

# Channel weights (reliability-based)
DEFAULT_CHANNEL_WEIGHTS = {
    -1002122751413: 0.8,  # New Pool Alert
    -1002126036544: 0.7,  # LP Chat
    -1002037135333: 0.6,  # New Token Bot
    -1002064472392: 0.5,  # Listing Bot
    -1002202241417: 0.9,  # GMGN Signals
}


class CrossChannelTracker:
    def __init__(self):
        self.data = self._load()
        self._ensure_structure()

    def _ensure_structure(self):
        if "tokens" not in self.data:
            self.data["tokens"] = {}
        if "channel_stats" not in self.data:
            self.data["channel_stats"] = {}
        for cid in CHANNEL_NAMES:
            cid_str = str(cid)
            if cid_str not in self.data["channel_stats"]:
                self.data["channel_stats"][cid_str] = {
                    "total_signals": 0,
                    "winners": 0,
                    "losers": 0,
                    "total_return": 0.0,
                }

    def _load(self):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            print(f"[cross_channel] save error: {e}")

    def record_token(self, ca: str, channel_id: int, token_data: dict):
        """Record token appearance in a channel."""
        if not ca or not channel_id:
            return

        ca_data = self.data["tokens"].get(ca, {
            "symbol": token_data.get("symbol", "?"),
            "channels": {},
            "first_seen": time.time(),
            "first_channel": channel_id,
            "status": "tracking",
            "ath_multiplier": 1.0,
        })

        channels = ca_data["channels"]
        cid_str = str(channel_id)
        now = time.time()

        if cid_str not in channels:
            channels[cid_str] = {
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "mcp_at_first": token_data.get("mcp", 0),
                "liq_at_first": token_data.get("liq_usd", 0),
                "holders_at_first": token_data.get("holders", 0),
                "signal_type": token_data.get("signal_type", ""),
            }
        else:
            channels[cid_str]["last_seen"] = now
            channels[cid_str]["count"] += 1

        # Update channel stats
        stats = self.data["channel_stats"].get(cid_str, {
            "total_signals": 0, "winners": 0, "losers": 0, "total_return": 0.0
        })
        stats["total_signals"] += 1
        self.data["channel_stats"][cid_str] = stats

        ca_data["symbol"] = token_data.get("symbol", ca_data.get("symbol", "?"))
        self.data["tokens"][ca] = ca_data

    def get_channel_count(self, ca: str) -> int:
        """How many different channels mentioned this CA."""
        return len(self.data.get("tokens", {}).get(ca, {}).get("channels", {}))

    def get_channel_list(self, ca: str) -> list:
        """List of channel IDs that mentioned this CA."""
        channels = self.data.get("tokens", {}).get(ca, {}).get("channels", {})
        return [int(cid) for cid in channels.keys()]

    def get_cross_channel_score(self, ca: str) -> dict:
        """
        Score based on cross-channel presence.
        Returns: {"score": float 0-1, "channels": int, "boost": float, "sources": list}
        """
        channels = self.data.get("tokens", {}).get(ca, {}).get("channels", {})
        n_channels = len(channels)

        if n_channels == 0:
            return {"score": 0, "channels": 0, "boost": 0, "sources": []}

        # Base score: logarithmic scale
        import math
        base_score = min(math.log2(n_channels + 1) / 3.0, 1.0)  # 1ch=0.33, 2ch=0.5, 3ch=0.67, 4ch=0.78, 5ch=0.87

        # Channel reliability weighting
        total_weight = 0
        max_weight = 0
        sources = []
        for cid_str, ch_data in channels.items():
            cid = int(cid_str)
            weight = DEFAULT_CHANNEL_WEIGHTS.get(cid, 0.5)
            stats = self.data["channel_stats"].get(cid_str, {})
            total_sigs = stats.get("total_signals", 0)
            winners = stats.get("winners", 0)
            if total_sigs > 10:
                reliability = winners / total_sigs
                weight = weight * 0.5 + reliability * 0.5
            total_weight += weight
            max_weight += 1.0
            sources.append(CHANNEL_NAMES.get(cid, f"Channel {cid}"))

        weighted_score = total_weight / max_weight if max_weight > 0 else 0

        # Final score: 60% base + 40% weighted
        final_score = base_score * 0.6 + weighted_score * 0.4

        # Boost: 2+ channels = significant boost
        boost = 0
        if n_channels >= 2:
            boost = 0.15 * (n_channels - 1)  # +15% per extra channel
        if n_channels >= 3:
            boost += 0.10  # extra 10% for 3+

        return {
            "score": min(final_score, 1.0),
            "channels": n_channels,
            "boost": min(boost, 0.35),
            "sources": sources,
        }

    def get_token_data_from_channels(self, ca: str) -> dict:
        """
        Merge data from all channels that mentioned this CA.
        Returns best available data.
        """
        channels = self.data.get("tokens", {}).get(ca, {}).get("channels", {})
        if not channels:
            return {}

        # Merge: take best values from each channel
        best = {
            "mcp": 0,
            "liq_usd": 0,
            "holders": 0,
            "signal_types": [],
            "channels": [],
        }

        for cid_str, ch_data in channels.items():
            cid = int(cid_str)
            if ch_data.get("mcp_at_first", 0) > best["mcp"]:
                best["mcp"] = ch_data["mcp_at_first"]
            if ch_data.get("liq_at_first", 0) > best["liq_usd"]:
                best["liq_usd"] = ch_data["liq_at_first"]
            if ch_data.get("holders_at_first", 0) > best["holders"]:
                best["holders"] = ch_data["holders_at_first"]
            if ch_data.get("signal_type"):
                best["signal_types"].append(ch_data["signal_type"])
            best["channels"].append(CHANNEL_NAMES.get(cid, f"Channel {cid}"))

        return best

    def record_outcome(self, ca: str, outcome: str, return_pct: float = 0):
        """Record whether a token was a winner or loser."""
        ca_data = self.data.get("tokens", {}).get(ca, {})
        if not ca_data:
            return

        ca_data["status"] = "winner" if outcome in ("winner", "mega_winner") else "loser"
        if return_pct:
            ca_data["ath_multiplier"] = max(1 + return_pct / 100, 0)

        # Update channel stats
        for cid_str in ca_data.get("channels", {}).keys():
            stats = self.data["channel_stats"].get(cid_str, {})
            if outcome in ("winner", "mega_winner"):
                stats["winners"] = stats.get("winners", 0) + 1
            elif outcome == "loser":
                stats["losers"] = stats.get("losers", 0) + 1
            stats["total_return"] = stats.get("total_return", 0) + return_pct
            self.data["channel_stats"][cid_str] = stats

        self.data["tokens"][ca] = ca_data

    def get_channel_reliability(self) -> dict:
        """Get reliability scores for all channels."""
        result = {}
        for cid_str, stats in self.data.get("channel_stats", {}).items():
            cid = int(cid_str)
            total = stats.get("total_signals", 0)
            winners = stats.get("winners", 0)
            avg_return = stats.get("total_return", 0) / max(total, 1)
            result[CHANNEL_NAMES.get(cid, f"Channel {cid}")] = {
                "total_signals": total,
                "winners": winners,
                "win_rate": winners / max(total, 1),
                "avg_return": avg_return,
            }
        return result

    def cleanup_old(self, days: int = 30) -> int:
        """Remove tokens older than N days."""
        cutoff = time.time() - (days * 86400)
        to_remove = []
        for ca, data in self.data.get("tokens", {}).items():
            if data.get("first_seen", 0) < cutoff:
                to_remove.append(ca)
        for ca in to_remove:
            del self.data["tokens"][ca]
        return len(to_remove)


# Singleton
_tracker = None

def get_tracker() -> CrossChannelTracker:
    global _tracker
    if _tracker is None:
        _tracker = CrossChannelTracker()
    return _tracker
