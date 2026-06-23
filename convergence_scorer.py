"""
convergence_scorer.py — Multi-Source Signal Convergence System

Combines signals from Telegram channels, DexScreener, pattern matching,
and climbing path into a unified confidence score.

Score = channel_bonus(25) + source_bonus(25) + recency(20) + momentum(15)
        + reliability(10) + pattern(5) × discovery_multiplier

Output: STRONG(80+) | HIGH(60+) | MEDIUM(40+) | LOW(20+) | WEAK(<20)
"""

import json
import os
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("convergence")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "convergence_data.json")


@dataclass
class TokenSighting:
    address: str
    symbol: str
    source_type: str          # "telegram" | "dexscreener" | "climbing" | "pattern" | "pre_migration"
    source_id: str            # channel_id, endpoint name, etc.
    source_name: str          # human-readable: "New Pool Alert", "boosted", etc.
    timestamp: float          # unix timestamp
    signal_type: str = ""     # "KOTH", "FDV_SURGE", "boosted", etc.
    features: dict = field(default_factory=dict)
    channel_weight: float = 1.0
    confidence_raw: float = 0.0


@dataclass
class TokenConvergence:
    address: str
    symbol: str
    sightings: list = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    convergence_score: float = 0.0
    confidence_level: str = ""
    recommended_action: str = ""
    reason: str = ""
    source_summary: str = ""
    channel_count: int = 0
    source_count: int = 0
    last_calculated: float = 0.0
    alerted: bool = False


class ConvergenceScorer:
    """Multi-source signal convergence scoring system."""

    def __init__(self, data_file: str = None):
        self.data_file = data_file or DATA_FILE
        self._sightings_map: dict = {}
        self._load()

    def _load(self):
        try:
            with open(self.data_file) as f:
                data = json.load(f)
            raw = data.get("sightings", {})
            for addr, entry in raw.items():
                conv = TokenConvergence(
                    address=addr,
                    symbol=entry.get("symbol", ""),
                    sightings=entry.get("sightings", []),
                    first_seen=entry.get("first_seen", 0),
                    last_seen=entry.get("last_seen", 0),
                    convergence_score=entry.get("convergence_score", 0),
                    confidence_level=entry.get("confidence_level", ""),
                    recommended_action=entry.get("recommended_action", ""),
                    reason=entry.get("reason", ""),
                    source_summary=entry.get("source_summary", ""),
                    channel_count=entry.get("channel_count", 0),
                    source_count=entry.get("source_count", 0),
                    last_calculated=entry.get("last_calculated", 0),
                    alerted=entry.get("alerted", False),
                )
                self._sightings_map[addr] = conv
        except (FileNotFoundError, json.JSONDecodeError):
            self._sightings_map = {}

    def _save(self):
        data = {
            "sightings": {},
            "last_cleanup": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "total_tracked": len(self._sightings_map),
                "high_convergence": sum(1 for c in self._sightings_map.values()
                                        if c.convergence_score >= 60),
            }
        }
        for addr, conv in self._sightings_map.items():
            data["sightings"][addr] = {
                "symbol": conv.symbol,
                "sightings": conv.sightings,
                "first_seen": conv.first_seen,
                "last_seen": conv.last_seen,
                "convergence_score": conv.convergence_score,
                "confidence_level": conv.confidence_level,
                "recommended_action": conv.recommended_action,
                "reason": conv.reason,
                "source_summary": conv.source_summary,
                "channel_count": conv.channel_count,
                "source_count": conv.source_count,
                "last_calculated": conv.last_calculated,
                "alerted": conv.alerted,
            }
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)

    # ── Public API ──────────────────────────────────────────

    def record_sighting(self, sighting: TokenSighting) -> TokenConvergence:
        """Record a new sighting and recalculate convergence score."""
        addr = sighting.address
        now = sighting.timestamp

        if addr not in self._sightings_map:
            self._sightings_map[addr] = TokenConvergence(
                address=addr, symbol=sighting.symbol,
            )

        conv = self._sightings_map[addr]
        conv.symbol = sighting.symbol

        # Add sighting
        conv.sightings.append({
            "source_type": sighting.source_type,
            "source_id": sighting.source_id,
            "source_name": sighting.source_name,
            "timestamp": sighting.timestamp,
            "signal_type": sighting.signal_type,
            "features": {k: v for k, v in sighting.features.items()
                         if isinstance(v, (int, float, str, bool))},
            "channel_weight": sighting.channel_weight,
            "confidence_raw": sighting.confidence_raw,
        })

        # Keep only last 24h of sightings
        cutoff = now - 86400
        conv.sightings = [s for s in conv.sightings if s["timestamp"] > cutoff]

        conv.first_seen = min(s["timestamp"] for s in conv.sightings)
        conv.last_seen = max(s["timestamp"] for s in conv.sightings)

        # Recalculate
        self.calculate_score(conv)
        self._save()
        return conv

    def get_convergence(self, address: str) -> Optional[TokenConvergence]:
        return self._sightings_map.get(address)

    def get_top_converging(self, min_score: float = 40, limit: int = 20) -> list:
        scored = [c for c in self._sightings_map.values()
                  if c.convergence_score >= min_score and not c.alerted]
        scored.sort(key=lambda x: -x.convergence_score)
        return scored[:limit]

    def mark_alerted(self, address: str):
        if address in self._sightings_map:
            self._sightings_map[address].alerted = True
            self._save()

    def calculate_score(self, conv: TokenConvergence) -> float:
        """Full convergence score calculation."""
        now = datetime.now(timezone.utc).timestamp()
        sightings = conv.sightings

        if not sightings:
            conv.convergence_score = 0
            conv.confidence_level = "WEAK"
            conv.recommended_action = ""
            return 0

        ch = self._channel_bonus(sightings)
        src = self._source_bonus(sightings)
        rec = self._recency_bonus(sightings, now)
        mom = self._momentum_bonus(sightings, now)
        rel = self._reliability_bonus(sightings)
        pat = self._pattern_bonus(sightings)

        raw = ch + src + rec + mom + rel + pat
        mult = self._discovery_multiplier(conv, now)
        final = min(100, raw * mult)

        conv.convergence_score = round(final, 1)
        conv.channel_count = len(set(
            s["source_id"] for s in sightings if s["source_type"] == "telegram"
        ))
        conv.source_count = len(set(s["source_type"] for s in sightings))
        conv.last_calculated = now

        level, action = self._confidence_level(final)
        conv.confidence_level = level
        conv.recommended_action = action

        reason, source_summary = self._build_reason(sightings)
        conv.reason = reason
        conv.source_summary = source_summary

        return final

    # ── Scoring Components ──────────────────────────────────

    def _channel_bonus(self, sightings: list) -> float:
        """Channel presence — NOT channel count. More channels != better."""
        channels = set()
        for s in sightings:
            if s.get("source_type") == "telegram":
                channels.add(s.get("source_id"))
        count = len(channels)
        if count == 0: return 0
        # Flat bonus: just being in a channel matters, not how many
        return 10

    def _source_bonus(self, sightings: list) -> float:
        """Source diversity — having different types helps, but not linearly."""
        types = set(s.get("source_type") for s in sightings)
        score = 5  # Base score for any source

        has_tg = "telegram" in types
        has_dex = "dexscreener" in types
        has_pat = "pattern" in types

        # Combo bonuses (learned from data)
        if has_tg and has_dex: score += 8
        if has_pat and (has_tg or has_dex): score += 5

        return min(15, score)

    def _recency_bonus(self, sightings: list, now: float) -> float:
        """More recent = stronger. Exponential decay."""
        if not sightings:
            return 0
        latest = max(s["timestamp"] for s in sightings)
        age = now - latest
        if age < 300:   return 20   # < 5 min
        if age < 900:   return 16   # < 15 min
        if age < 1800:  return 12   # < 30 min
        if age < 3600:  return 8    # < 1 hour
        if age < 7200:  return 4    # < 2 hour
        return 0

    def _momentum_bonus(self, sightings: list, now: float) -> float:
        """More sightings over time = building interest."""
        if len(sightings) < 2:
            return 0
        recent = [s for s in sightings if now - s["timestamp"] < 7200]
        count = len(recent)
        if count == 0: return 0
        if count == 1: return 3
        if count == 2: return 7
        if count == 3: return 11
        if count == 4: return 13
        return 15

    def _reliability_bonus(self, sightings: list) -> float:
        """Average channel weight."""
        tg = [s for s in sightings if s.get("source_type") == "telegram"]
        if not tg:
            return 5
        avg = sum(s.get("channel_weight", 1.0) for s in tg) / len(tg)
        return min(10, max(0, avg * 5))

    def _pattern_bonus(self, sightings: list) -> float:
        """Pattern match confirmation."""
        pat = [s for s in sightings if s.get("source_type") == "pattern"]
        if not pat:
            return 0
        best = max(s.get("confidence_raw", 0) for s in pat)
        return min(5, best * 5)

    def _discovery_multiplier(self, conv: TokenConvergence, now: float) -> float:
        """New discovery bonus, late signal penalty."""
        age = now - conv.first_seen
        if age < 1800:  return 1.2   # < 30 min: 20% bonus
        if age < 7200:  return 1.0   # 30 min - 2h: normal
        return 0.8                    # > 2h: 20% penalty

    def _confidence_level(self, score: float) -> tuple:
        if score >= 80: return "STRONG", "strong_signal"
        if score >= 60: return "HIGH", "signal"
        if score >= 40: return "MEDIUM", "watch"
        if score >= 20: return "LOW", "watch"
        return "WEAK", ""

    def _build_reason(self, sightings: list) -> tuple:
        tg_channels = set()
        dex_sources = set()
        has_pattern = False
        has_climbing = False
        has_premig = False

        for s in sightings:
            st = s.get("source_type", "")
            if st == "telegram":
                tg_channels.add(s.get("source_name", "?"))
            elif st == "dexscreener":
                dex_sources.add(s.get("source_name", "?"))
            elif st == "pattern":
                has_pattern = True
            elif st == "climbing":
                has_climbing = True
            elif st == "pre_migration":
                has_premig = True

        parts = []
        source_parts = []

        if tg_channels:
            source_parts.append(f"TG({len(tg_channels)})")
            parts.append(f"Telegram: {', '.join(list(tg_channels)[:3])}")
        if dex_sources:
            source_parts.append(f"Dex({len(dex_sources)})")
            parts.append(f"DexScreener: {', '.join(list(dex_sources)[:3])}")
        if has_pattern:
            source_parts.append("Pattern")
            parts.append("Pattern match confirmed")
        if has_climbing:
            source_parts.append("Climbing")
            parts.append("Price momentum detected")
        if has_premig:
            source_parts.append("Pre-migration")
            parts.append("Early launch detected")

        reason = " + ".join(parts) if parts else "Single source"
        source_summary = " + ".join(source_parts) if source_parts else "Unknown"
        return reason, source_summary

    # ── Cleanup ─────────────────────────────────────────────

    def cleanup_old(self, max_age: float = 86400):
        """Remove sightings older than max_age (default 24h)."""
        now = datetime.now(timezone.utc).timestamp()
        removed = 0
        for addr in list(self._sightings_map.keys()):
            conv = self._sightings_map[addr]
            conv.sightings = [s for s in conv.sightings if now - s["timestamp"] < max_age]
            if not conv.sightings:
                del self._sightings_map[addr]
                removed += 1
            else:
                conv.first_seen = min(s["timestamp"] for s in conv.sightings)
                conv.last_seen = max(s["timestamp"] for s in conv.sightings)
                self.calculate_score(conv)
        self._save()
        return removed

    def get_report(self) -> str:
        """Human-readable convergence report."""
        if not self._sightings_map:
            return "📊 No convergence data yet. Tracking will begin as signals arrive."

        top = sorted(self._sightings_map.values(),
                     key=lambda x: -x.convergence_score)[:15]

        lines = ["📊 Convergence Report", "=" * 40]
        for c in top:
            emoji = {"STRONG": "🔥", "HIGH": "✅", "MEDIUM": "👀",
                     "LOW": "⬇️", "WEAK": "💤"}.get(c.confidence_level, "")
            lines.append(
                f"{emoji} {c.symbol:12s} | Score: {c.convergence_score:5.1f} | "
                f"{c.confidence_level:6s} | Ch: {c.channel_count} Src: {c.source_count}"
            )
            lines.append(f"   📡 {c.source_summary}")
            lines.append(f"   🧠 {c.reason}")

        lines.append(f"\n{'=' * 40}")
        lines.append(f"Tracked: {len(self._sightings_map)} | "
                     f"High: {sum(1 for c in self._sightings_map.values() if c.convergence_score >= 60)}")
        return "\n".join(lines)
