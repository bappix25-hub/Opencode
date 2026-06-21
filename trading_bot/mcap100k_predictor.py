import json
import os
import time
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from dataclasses import dataclass, asdict, field

logger = logging.getLogger("mcap100k")

MCAP100K_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "mcap100k_learning.json")

TARGET_MCAP = 100_000
SIGNAL_WINDOW_SECONDS = 600  # 10 minutes


@dataclass
class TokenLaunchProfile:
    symbol: str
    address: str
    launch_time: float
    features_at_launch: dict = field(default_factory=dict)
    reached_100k: bool = False
    peak_mcap: float = 0.0
    time_to_100k: float = 0.0
    final_mcap: float = 0.0
    tracked: bool = True
    resolved: bool = False


@dataclass
class TimingSlot:
    hour: int
    day_of_week: int
    total_signals: int = 0
    successful_signals: int = 0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


class MCap100kPredictor:
    def __init__(self):
        self.active_tokens: Dict[str, TokenLaunchProfile] = {}
        self.resolved_tokens: List[TokenLaunchProfile] = []
        self.timing_slots: Dict[str, TimingSlot] = {}
        self.pattern_weights: Dict[str, float] = {}
        self.feature_importance: Dict[str, float] = {}
        self._load_data()

    def _load_data(self):
        try:
            if os.path.exists(MCAP100K_DATA_FILE):
                with open(MCAP100K_DATA_FILE, "r") as f:
                    data = json.load(f)
                for t in data.get("resolved_tokens", []):
                    self.resolved_tokens.append(TokenLaunchProfile(**t))
                for key, val in data.get("timing_slots", {}).items():
                    self.timing_slots[key] = TimingSlot(**val)
                self.pattern_weights = data.get("pattern_weights", {})
                self.feature_importance = data.get("feature_importance", {})
                logger.info(
                    f"MCap100k loaded: {len(self.resolved_tokens)} resolved, "
                    f"{sum(1 for t in self.resolved_tokens if t.reached_100k)} reached 100K"
                )
        except Exception as e:
            logger.error(f"MCap100k load error: {e}")

    def save_data(self):
        try:
            os.makedirs(os.path.dirname(MCAP100K_DATA_FILE), exist_ok=True)
            data = {
                "resolved_tokens": [asdict(t) for t in self.resolved_tokens[-500:]],
                "timing_slots": {k: asdict(v) for k, v in self.timing_slots.items()},
                "pattern_weights": self.pattern_weights,
                "feature_importance": self.feature_importance,
                "saved_at": time.time(),
            }
            with open(MCAP100K_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"MCap100k save error: {e}")

    def register_new_token(self, symbol: str, address: str, metrics: dict) -> Optional[TokenLaunchProfile]:
        now = time.time()
        age = metrics.get("age_seconds", 0)
        if age > SIGNAL_WINDOW_SECONDS:
            return None

        if address in self.active_tokens:
            return None

        features = self._extract_launch_features(metrics, now)
        profile = TokenLaunchProfile(
            symbol=symbol,
            address=address,
            launch_time=now,
            features_at_launch=features,
        )
        self.active_tokens[address] = profile
        logger.info(f"🔍 TRACKING {symbol} for $100K MC | Features: {list(features.keys())}")
        return profile

    def _extract_launch_features(self, metrics: dict, now: float) -> dict:
        return {
            "liquidity": metrics.get("liquidity", 0),
            "fdv": metrics.get("fdv", 0),
            "volume_5m": metrics.get("volume_5m", 0),
            "volume_1h": metrics.get("volume_1h", 0),
            "buyers_5m": metrics.get("transactions", {}).get("m5", {}).get("buyers", 0),
            "sellers_5m": metrics.get("transactions", {}).get("m5", {}).get("sellers", 0),
            "buyers_1h": metrics.get("transactions", {}).get("h1", {}).get("buyers", 0),
            "sellers_1h": metrics.get("transactions", {}).get("h1", {}).get("sellers", 0),
            "lp_count": metrics.get("lp_count", 0),
            "holder_count": metrics.get("holder_count", 0),
            "price_change_5m": metrics.get("price_change_5m", 0),
            "price_change_1h": metrics.get("price_change_1h", 0),
            "bonding_curve_progress": metrics.get("bonding_curve_progress", 0),
            "unique_5m": metrics.get("transactions", {}).get("m5", {}).get("buyers", 0) + metrics.get("transactions", {}).get("m5", {}).get("sellers", 0),
            "vol_liq_ratio": metrics.get("volume_5m", 0) / max(metrics.get("liquidity", 1), 1),
            "buy_sell_ratio_5m": (metrics.get("transactions", {}).get("m5", {}).get("buyers", 0) / max(metrics.get("transactions", {}).get("m5", {}).get("sellers", 1), 1)),
        }

    def update_token_metrics(self, address: str, metrics: dict) -> Optional[dict]:
        if address not in self.active_tokens:
            return None

        profile = self.active_tokens[address]
        current_mcap = metrics.get("fdv", 0)

        if current_mcap > profile.peak_mcap:
            profile.peak_mcap = current_mcap

        if not profile.reached_100k and current_mcap >= TARGET_MCAP:
            profile.reached_100k = True
            profile.time_to_100k = time.time() - profile.launch_time
            logger.info(
                f"🎯 $100K MC HIT! {profile.symbol} in {profile.time_to_100k:.0f}s | "
                f"Peak: ${current_mcap:,.0f}"
            )

        profile.final_mcap = current_mcap

        age = time.time() - profile.launch_time
        if age > 3600 and not profile.resolved:
            profile.resolved = True
            self.resolved_tokens.append(profile)
            self._update_learning_from_outcome(profile)
            del self.active_tokens[address]
            return {
                "symbol": profile.symbol,
                "reached_100k": profile.reached_100k,
                "peak_mcap": profile.peak_mcap,
                "time_to_100k": profile.time_to_100k,
                "final_mcap": profile.final_mcap,
            }

        return None

    def _update_learning_from_outcome(self, profile: TokenLaunchProfile):
        features = profile.features_at_launch
        reached = profile.reached_100k

        for feat_name, feat_val in features.items():
            if isinstance(feat_val, (int, float)):
                key = feat_name
                if key not in self.feature_importance:
                    self.feature_importance[key] = {"total": 0, "success": 0, "avg_val_success": 0, "avg_val_fail": 0}

                fi = self.feature_importance[key]
                fi["total"] += 1
                if reached:
                    fi["success"] += 1
                    fi["avg_val_success"] = (fi["avg_val_success"] * (fi["success"] - 1) + feat_val) / fi["success"]
                else:
                    fail_count = fi["total"] - fi["success"]
                    fi["avg_val_fail"] = (fi["avg_val_fail"] * (fail_count - 1) + feat_val) / max(fail_count, 1)

        now = time.localtime()
        slot_key = f"{now.tm_hour}_{now.tm_wday}"
        if slot_key not in self.timing_slots:
            self.timing_slots[slot_key] = TimingSlot(hour=now.tm_hour, day_of_week=now.tm_wday)

        slot = self.timing_slots[slot_key]
        slot.total_signals += 1
        if reached:
            slot.successful_signals += 1
        slot.win_rate = slot.successful_signals / max(slot.total_signals, 1)

        self._update_pattern_weights(profile, reached)
        self.save_data()

    def _update_pattern_weights(self, profile: TokenLaunchProfile, reached: bool):
        features = profile.features_at_launch

        if features.get("liquidity", 0) > 0:
            liq = features["liquidity"]
            if liq < 1000:
                bucket = "liq_micro"
            elif liq < 5000:
                bucket = "liq_low"
            elif liq < 20000:
                bucket = "liq_mid"
            else:
                bucket = "liq_high"
            self._update_weight(bucket, reached)

        vol_liq = features.get("vol_liq_ratio", 0)
        if vol_liq > 0:
            if vol_liq > 1.0:
                self._update_weight("high_vol_liq", reached)
            elif vol_liq > 0.3:
                self._update_weight("mid_vol_liq", reached)

        buyers = features.get("buyers_5m", 0)
        sellers = features.get("sellers_5m", 0)
        if buyers > 0 and sellers == 0:
            self._update_weight("all_buyers", reached)
        elif buyers > sellers * 3:
            self._update_weight("strong_buying", reached)

        bc = features.get("bonding_curve_progress", 0)
        if bc > 80:
            self._update_weight("near_migration", reached)
        elif bc > 50:
            self._update_weight("building", reached)

        if features.get("lp_count", 0) >= 3:
            self._update_weight("multi_lp", reached)

    def _update_weight(self, pattern: str, success: bool):
        if pattern not in self.pattern_weights:
            self.pattern_weights[pattern] = {"count": 0, "successes": 0, "weight": 0.5}

        pw = self.pattern_weights[pattern]
        pw["count"] += 1
        if success:
            pw["successes"] += 1

        total = pw["count"]
        win_rate = pw["successes"] / max(total, 1)

        if total >= 5:
            pw["weight"] = win_rate
        else:
            pw["weight"] = 0.5 * win_rate + 0.5 * 0.5

    def predict_100k_probability(self, metrics: dict) -> float:
        features = self._extract_launch_features(metrics, time.time())
        scores = []

        for feat_name, feat_val in features.items():
            if isinstance(feat_val, (int, float)) and feat_name in self.feature_importance:
                fi = self.feature_importance[feat_name]
                if fi["total"] >= 10:
                    base_rate = fi["success"] / max(fi["total"], 1)
                    if feat_val > fi["avg_val_success"] * 0.5:
                        scores.append(base_rate * 1.2)
                    else:
                        scores.append(base_rate * 0.8)

        if features.get("liquidity", 0) > 0:
            liq = features["liquidity"]
            if liq < 1000:
                scores.append(0.15)
            elif liq < 5000:
                scores.append(0.35)
            elif liq < 20000:
                scores.append(0.55)
            else:
                scores.append(0.70)

        vol_liq = features.get("vol_liq_ratio", 0)
        if vol_liq > 1.0:
            scores.append(0.7)
        elif vol_liq > 0.3:
            scores.append(0.5)
        else:
            scores.append(0.3)

        buyers = features.get("buyers_5m", 0)
        sellers = features.get("sellers_5m", 0)
        if buyers >= 5 and sellers <= 1:
            scores.append(0.65)
        elif buyers > sellers * 2:
            scores.append(0.5)
        else:
            scores.append(0.3)

        for pattern, pw in self.pattern_weights.items():
            if pw["count"] >= 5:
                feat_val = self._get_pattern_match(pattern, features)
                if feat_val:
                    scores.append(pw["weight"])

        if not scores:
            return 0.2

        return min(max(np.mean(scores), 0.0), 1.0)

    def _get_pattern_match(self, pattern: str, features: dict) -> bool:
        if pattern == "liq_micro":
            return features.get("liquidity", 0) < 1000
        elif pattern == "liq_low":
            return 1000 <= features.get("liquidity", 0) < 5000
        elif pattern == "liq_mid":
            return 5000 <= features.get("liquidity", 0) < 20000
        elif pattern == "liq_high":
            return features.get("liquidity", 0) >= 20000
        elif pattern == "high_vol_liq":
            return features.get("vol_liq_ratio", 0) > 1.0
        elif pattern == "mid_vol_liq":
            return 0.3 < features.get("vol_liq_ratio", 0) <= 1.0
        elif pattern == "all_buyers":
            return features.get("buyers_5m", 0) > 0 and features.get("sellers_5m", 0) == 0
        elif pattern == "strong_buying":
            return features.get("buyers_5m", 0) > features.get("sellers_5m", 0) * 3
        elif pattern == "near_migration":
            return features.get("bonding_curve_progress", 0) > 80
        elif pattern == "building":
            return features.get("bonding_curve_progress", 0) > 50
        elif pattern == "multi_lp":
            return features.get("lp_count", 0) >= 3
        return False

    def is_good_timing(self) -> Tuple[bool, str]:
        now = time.localtime()
        slot_key = f"{now.tm_hour}_{now.tm_wday}"

        if slot_key in self.timing_slots:
            slot = self.timing_slots[slot_key]
            if slot.total_signals >= 3 and slot.win_rate >= 0.3:
                return True, f"Hour {now.tm_hour}: {slot.win_rate:.0%} win rate ({slot.total_signals} signals)"

        hour_key = f"{now.tm_hour}"
        hour_slots = [v for k, v in self.timing_slots.items() if k.startswith(hour_key)]
        if hour_slots:
            avg_wr = np.mean([s.win_rate for s in hour_slots])
            total = sum(s.total_signals for s in hour_slots)
            if total >= 5 and avg_wr >= 0.25:
                return True, f"Hour {now.tm_hour}: avg {avg_wr:.0%} win rate"

        return False, f"Hour {now.tm_hour}: insufficient data"

    def prune_weak_patterns(self, min_occurrences: int = 10, min_win_rate: float = 0.15):
        pruned = []
        for pattern in list(self.pattern_weights.keys()):
            pw = self.pattern_weights[pattern]
            if pw["count"] >= min_occurrences and pw["weight"] < min_win_rate:
                pruned.append(pattern)
                del self.pattern_weights[pattern]

        if pruned:
            logger.info(f"🗑️ Pruned {len(pruned)} weak patterns: {pruned}")
            self.save_data()

        return pruned

    def get_learning_summary(self) -> dict:
        total_resolved = len(self.resolved_tokens)
        reached = sum(1 for t in self.resolved_tokens if t.reached_100k)
        active = len(self.active_tokens)

        avg_time = 0
        times = [t.time_to_100k for t in self.resolved_tokens if t.reached_100k and t.time_to_100k > 0]
        if times:
            avg_time = np.mean(times)

        top_patterns = sorted(
            [(k, v) for k, v in self.pattern_weights.items() if v["count"] >= 5],
            key=lambda x: x[1]["weight"],
            reverse=True
        )[:10]

        return {
            "total_tracked": total_resolved,
            "reached_100k": reached,
            "reach_rate": reached / max(total_resolved, 1),
            "active_tracking": active,
            "avg_time_to_100k_seconds": avg_time,
            "top_patterns": top_patterns,
            "feature_importance": {
                k: {"win_rate": v["success"] / max(v["total"], 1), "count": v["total"]}
                for k, v in self.feature_importance.items()
                if v["total"] >= 5
            },
        }


mcap100k_predictor = MCap100kPredictor()
