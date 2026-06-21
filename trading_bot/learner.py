import json
import os
import logging
import time
import math
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("learner")

LEARNING_FILE = os.path.join(os.path.dirname(__file__), "data", "learning.json")


@dataclass
class CoinProfile:
    symbol: str
    coin_type: str = "unknown"
    avg_pump_pct: float = 0.0
    avg_dump_pct: float = 0.0
    avg_hold_time: float = 0.0
    best_tp_pct: float = 50.0
    best_sl_pct: float = -25.0
    win_rate: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    last_updated: float = 0.0


@dataclass
class Pattern:
    name: str
    condition: dict
    success_rate: float = 0.0
    occurrences: int = 0
    successes: int = 0
    recommended_tp: float = 50.0
    recommended_sl: float = -25.0


@dataclass
class MarketCondition:
    name: str
    avg_pump: float = 0.0
    avg_hold: float = 0.0
    win_rate: float = 0.0
    sample_count: int = 0


class Learner:
    def __init__(self):
        self.coin_profiles: Dict[str, CoinProfile] = {}
        self.patterns: List[Pattern] = []
        self.market_conditions: Dict[str, MarketCondition] = {}
        self.trade_outcomes: list = []
        self.model_stats = {
            "total_outcomes": 0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "best_pattern": "",
            "accuracy_trend": [],
        }
        self._load_data()

    def _load_data(self):
        try:
            if os.path.exists(LEARNING_FILE):
                with open(LEARNING_FILE, "r") as f:
                    data = json.load(f)

                for symbol, profile in data.get("coin_profiles", {}).items():
                    self.coin_profiles[symbol] = CoinProfile(**profile)

                for pattern in data.get("patterns", []):
                    self.patterns.append(Pattern(**pattern))

                for name, cond in data.get("market_conditions", {}).items():
                    self.market_conditions[name] = MarketCondition(**cond)

                self.trade_outcomes = data.get("trade_outcomes", [])
                self.model_stats = data.get("model_stats", self.model_stats)

                logger.info(
                    f"Learning data loaded: {len(self.coin_profiles)} coins, "
                    f"{len(self.patterns)} patterns, {len(self.trade_outcomes)} outcomes"
                )
        except Exception as e:
            logger.error(f"Learning data load error: {e}")

    def _save_data(self):
        try:
            os.makedirs(os.path.dirname(LEARNING_FILE), exist_ok=True)
            data = {
                "coin_profiles": {k: asdict(v) for k, v in self.coin_profiles.items()},
                "patterns": [asdict(p) for p in self.patterns],
                "market_conditions": {k: asdict(v) for k, v in self.market_conditions.items()},
                "trade_outcomes": self.trade_outcomes[-500:],
                "model_stats": self.model_stats,
            }
            with open(LEARNING_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Learning data save error: {e}")

    def record_signal_outcome(
        self,
        symbol: str,
        address: str,
        score: float,
        signals: list,
        entry_price: float,
        highest_price: float,
        lowest_price: float,
        current_price: float,
        metrics: dict,
        signal_type: str = "pump",
    ):
        if entry_price <= 0 or current_price <= 0:
            return

        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        max_pnl_pct = ((highest_price - entry_price) / entry_price * 100) if highest_price > entry_price else 0
        is_win = pnl_pct > 0

        outcome = {
            "symbol": symbol,
            "address": address,
            "coin_type": self.classify_coin(metrics),
            "entry_price": entry_price,
            "exit_price": current_price,
            "highest_price": highest_price,
            "pnl_pct": pnl_pct,
            "max_pnl_pct": max_pnl_pct,
            "pump_score": score,
            "signals": signals,
            "is_win": is_win,
            "features": self.extract_features(metrics, score),
            "signal_type": signal_type,
            "tp_pct": max_pnl_pct if max_pnl_pct > 0 else pnl_pct,
            "sl_pct": pnl_pct if pnl_pct < 0 else -25.0,
            "hold_time": time.time() - (metrics.get("signal_timestamp", time.time())),
            "timestamp": time.time(),
        }
        self.trade_outcomes.append(outcome)

        coin_type = self.classify_coin(metrics)
        self._update_coin_profile(symbol, is_win, pnl_pct, 0, 0, 0)
        self._update_coin_type_profile(coin_type, is_win, pnl_pct, 0, 0, 0)
        self._update_patterns(outcome)
        self._update_model_stats(outcome)

        self._save_data()
        logger.info(
            f"Signal outcome: {symbol} ({coin_type}) | "
            f"PnL: {pnl_pct:+.1f}% | Max: +{max_pnl_pct:.1f}% | "
            f"{'WIN' if is_win else 'LOSS'} | "
            f"Score: {score:.2f}"
        )

    def get_pre_pump_indicators(self, metrics: dict) -> dict:
        coin_type = self.classify_coin(metrics)
        outcomes = [
            o for o in self.trade_outcomes
            if o.get("coin_type") == coin_type
        ]
        if len(outcomes) < 5:
            return {"confidence": 0.5, "recommended_entry_vol": 1000, "recommended_min_wallets": 10}

        wins = [o for o in outcomes if o["is_win"]]
        win_rate = len(wins) / len(outcomes) if outcomes else 0.5

        avg_features = {}
        for key in ["volume_5m", "liquidity", "fdv", "price_change_5m"]:
            vals = [o.get("features", {}).get(key, 0) for o in outcomes if o.get("features", {}).get(key, 0) > 0]
            if vals:
                avg_features[key] = sum(vals) / len(vals)

        confidence = min(max(win_rate, 0.3), 0.9)
        rec_vol = avg_features.get("volume_5m", 1000) * 0.5
        rec_wallets = 15 if win_rate > 0.6 else 20

        return {
            "confidence": round(confidence, 2),
            "recommended_entry_vol": max(rec_vol, 500),
            "recommended_min_wallets": rec_wallets,
            "win_rate": round(win_rate * 100, 1),
            "sample_size": len(outcomes),
        }

    def classify_coin(self, metrics: dict) -> str:
        age = metrics.get("age_seconds", 999999)
        liquidity = metrics.get("liquidity", 0)
        volume_5m = metrics.get("volume_5m", 0)
        fdv = metrics.get("fdv", 0)

        if age < 300 and liquidity < 5000:
            return "micro_new"
        elif age < 1800 and liquidity < 20000:
            return "new_low_liq"
        elif age < 3600 and liquidity < 100000:
            return "new_mid_liq"
        elif volume_5m > 500000 and liquidity > 500000:
            return "blue_chip"
        elif volume_5m > 100000:
            return "high_volume"
        elif fdv > 5000000:
            return "established"
        else:
            return "standard"

    def extract_features(self, metrics: dict, pump_score: float) -> dict:
        return {
            "coin_type": self.classify_coin(metrics),
            "pump_score": pump_score,
            "liquidity": metrics.get("liquidity", 0),
            "volume_5m": metrics.get("volume_5m", 0),
            "volume_1h": metrics.get("volume_1h", 0),
            "fdv": metrics.get("fdv", 0),
            "price_change_5m": metrics.get("price_change_5m", 0),
            "price_change_1h": metrics.get("price_change_1h", 0),
            "age_seconds": metrics.get("age_seconds", 0),
            "liquidity_change": metrics.get("liquidity_change", 0),
            "volume_change": metrics.get("volume_change", 0),
        }

    def get_recommended_tp_sl(
        self, symbol: str, metrics: dict, pump_score: float
    ) -> tuple:
        coin_type = self.classify_coin(metrics)
        profile = self.coin_profiles.get(symbol)
        coin_profile = self.coin_profiles.get(coin_type)

        base_tp = 50.0
        base_sl = -25.0

        if profile and profile.total_trades >= 3:
            base_tp = profile.best_tp_pct
            base_sl = profile.best_sl_pct
            logger.debug(f"Using learned TP/SL for {symbol}: {base_tp}/{base_sl}")
        elif coin_profile and coin_profile.total_trades >= 5:
            base_tp = coin_profile.best_tp_pct
            base_sl = coin_profile.best_sl_pct
            logger.debug(f"Using type TP/SL for {coin_type}: {base_tp}/{base_sl}")
        else:
            base_tp, base_sl = self._get_default_by_type(coin_type)

        base_tp, base_sl = self._adjust_by_conditions(
            base_tp, base_sl, metrics, pump_score, coin_type
        )

        matching = self._find_matching_patterns(metrics)
        if matching:
            best = max(matching, key=lambda x: x.success_rate)
            if best.success_rate > 0.5:
                blend = 0.3
                base_tp = base_tp * (1 - blend) + best.recommended_tp * blend
                base_sl = base_sl * (1 - blend) + best.recommended_sl * blend
                logger.debug(
                    f"Pattern adjustment: {best.name} "
                    f"(rate={best.success_rate:.2f}) -> TP={base_tp:.1f} SL={base_sl:.1f}"
                )

        base_tp = max(min(base_tp, 500), 10)
        base_sl = max(min(base_sl, -5), -80)

        return round(base_tp, 1), round(base_sl, 1)

    def _get_default_by_type(self, coin_type: str) -> tuple:
        defaults = {
            "micro_new": (80, -40),
            "new_low_liq": (60, -30),
            "new_mid_liq": (50, -25),
            "blue_chip": (30, -15),
            "high_volume": (40, -20),
            "established": (25, -12),
            "standard": (50, -25),
        }
        return defaults.get(coin_type, (50, -25))

    def _adjust_by_conditions(
        self, tp: float, sl: float, metrics: dict, pump_score: float, coin_type: str
    ) -> tuple:
        if pump_score >= 0.8:
            tp *= 1.3
            sl *= 0.85
        elif pump_score >= 0.6:
            tp *= 1.15
            sl *= 0.9

        liq = metrics.get("liquidity", 0)
        if liq > 100000:
            tp *= 1.1
        elif liq < 10000:
            tp *= 0.7
            sl *= 0.7

        vol_change = metrics.get("volume_change", 0)
        if vol_change > 200:
            tp *= 1.2

        liq_change = metrics.get("liquidity_change", 0)
        if liq_change > 50:
            tp *= 1.15
        elif liq_change < -30:
            sl *= 0.8

        age = metrics.get("age_seconds", 999)
        if age < 120:
            tp *= 1.2
        elif age < 300:
            tp *= 1.1

        if coin_type in ("blue_chip", "established"):
            tp *= 0.8
            sl *= 1.1

        return tp, sl

    def _find_matching_patterns(self, metrics: dict) -> List[Pattern]:
        matching = []
        features = self.extract_features(metrics, 0.5)

        for pattern in self.patterns:
            if self._pattern_matches(pattern, features):
                matching.append(pattern)

        return matching

    def _pattern_matches(self, pattern: Pattern, features: dict) -> bool:
        cond = pattern.condition
        for key, value in cond.items():
            if key not in features:
                continue

            feat_val = features[key]
            if isinstance(value, dict):
                min_val = value.get("min", float("-inf"))
                max_val = value.get("max", float("inf"))
                if not (min_val <= feat_val <= max_val):
                    return False
            elif feat_val != value:
                return False

        return True

    def record_trade_outcome(
        self,
        symbol: str,
        coin_type: str,
        entry_price: float,
        exit_price: float,
        hold_time: float,
        tp_pct: float,
        sl_pct: float,
        pump_score: float,
        metrics: dict,
        reason: str,
    ):
        if entry_price <= 0 or exit_price <= 0:
            return

        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        is_win = pnl_pct > 0

        outcome = {
            "symbol": symbol,
            "coin_type": coin_type,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "hold_time": hold_time,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "pump_score": pump_score,
            "reason": reason,
            "is_win": is_win,
            "features": self.extract_features(metrics, pump_score),
            "timestamp": time.time(),
        }
        self.trade_outcomes.append(outcome)

        self._update_coin_profile(symbol, is_win, pnl_pct, hold_time, tp_pct, sl_pct)
        self._update_coin_type_profile(coin_type, is_win, pnl_pct, hold_time, tp_pct, sl_pct)
        self._update_patterns(outcome)
        self._update_model_stats(outcome)

        self._save_data()
        logger.info(
            f"📝 Learning: {symbol} ({coin_type}) | "
            f"PnL: {pnl_pct:+.1f}% | {'WIN' if is_win else 'LOSS'} | "
            f"Hold: {hold_time:.0f}s | TP: {tp_pct:.0f}% SL: {sl_pct:.0f}%"
        )

    def _update_coin_profile(
        self, symbol: str, is_win: bool, pnl_pct: float,
        hold_time: float, tp_pct: float, sl_pct: float
    ):
        if symbol not in self.coin_profiles:
            self.coin_profiles[symbol] = CoinProfile(symbol=symbol)

        p = self.coin_profiles[symbol]
        n = p.total_trades

        p.total_trades += 1
        if is_win:
            p.wins += 1
        else:
            p.losses += 1

        p.win_rate = (p.wins / p.total_trades) * 100

        p.avg_pump_pct = (p.avg_pump_pct * n + pnl_pct) / (n + 1) if pnl_pct > 0 else p.avg_pump_pct
        p.avg_dump_pct = (p.avg_dump_pct * n + pnl_pct) / (n + 1) if pnl_pct < 0 else p.avg_dump_pct
        p.avg_hold_time = (p.avg_hold_time * n + hold_time) / (n + 1)

        if p.total_trades >= 3:
            outcomes = [
                o for o in self.trade_outcomes
                if o["symbol"] == symbol
            ]
            if outcomes:
                wins = [o for o in outcomes if o["is_win"]]
                if wins:
                    avg_tp = sum(o["tp_pct"] for o in wins) / len(wins)
                    p.best_tp_pct = avg_tp
                losses = [o for o in outcomes if not o["is_win"]]
                if losses:
                    avg_sl = sum(o["sl_pct"] for o in losses) / len(losses)
                    p.best_sl_pct = avg_sl

        p.last_updated = time.time()

    def _update_coin_type_profile(
        self, coin_type: str, is_win: bool, pnl_pct: float,
        hold_time: float, tp_pct: float, sl_pct: float
    ):
        if coin_type not in self.coin_profiles:
            self.coin_profiles[coin_type] = CoinProfile(symbol=coin_type)

        p = self.coin_profiles[coin_type]
        n = p.total_trades

        p.total_trades += 1
        if is_win:
            p.wins += 1
        else:
            p.losses += 1

        p.win_rate = (p.wins / p.total_trades) * 100
        p.avg_pump_pct = (p.avg_pump_pct * n + pnl_pct) / (n + 1) if pnl_pct > 0 else p.avg_pump_pct
        p.avg_dump_pct = (p.avg_dump_pct * n + pnl_pct) / (n + 1) if pnl_pct < 0 else p.avg_dump_pct
        p.avg_hold_time = (p.avg_hold_time * n + hold_time) / (n + 1)

        if p.total_trades >= 5:
            outcomes = [
                o for o in self.trade_outcomes
                if o["coin_type"] == coin_type
            ]
            if outcomes:
                wins = [o for o in outcomes if o["is_win"]]
                if wins:
                    avg_tp = sum(o["tp_pct"] for o in wins) / len(wins)
                    p.best_tp_pct = avg_tp
                losses = [o for o in outcomes if not o["is_win"]]
                if losses:
                    avg_sl = sum(o["sl_pct"] for o in losses) / len(losses)
                    p.best_sl_pct = avg_sl

        p.last_updated = time.time()

    def _update_patterns(self, outcome: dict):
        features = outcome["features"]
        is_win = outcome["is_win"]

        pattern_conditions = [
            {"coin_type": features["coin_type"], "pump_score": {"min": 0.7, "max": 1.0}},
            {"coin_type": features["coin_type"], "liquidity": {"min": 0, "max": 10000}},
            {"coin_type": features["coin_type"], "liquidity": {"min": 10000, "max": 50000}},
            {"coin_type": features["coin_type"], "volume_change": {"min": 100, "max": 99999}},
            {"coin_type": features["coin_type"], "age_seconds": {"min": 0, "max": 300}},
            {"coin_type": features["coin_type"], "price_change_5m": {"min": 5, "max": 999}},
        ]

        for cond in pattern_conditions:
            if self._check_condition(cond, features):
                existing = None
                for p in self.patterns:
                    if p.condition == cond:
                        existing = p
                        break

                if existing:
                    existing.occurrences += 1
                    if is_win:
                        existing.successes += 1
                    existing.success_rate = existing.successes / existing.occurrences

                    n = existing.occurrences
                    existing.recommended_tp = (
                        existing.recommended_tp * (n - 1) + outcome["tp_pct"]
                    ) / n
                    existing.recommended_sl = (
                        existing.recommended_sl * (n - 1) + outcome["sl_pct"]
                    ) / n
                else:
                    self.patterns.append(
                        Pattern(
                            name=f"pattern_{len(self.patterns)}",
                            condition=cond,
                            occurrences=1,
                            successes=1 if is_win else 0,
                            success_rate=1.0 if is_win else 0.0,
                            recommended_tp=outcome["tp_pct"],
                            recommended_sl=outcome["sl_pct"],
                        )
                    )

    def _check_condition(self, cond: dict, features: dict) -> bool:
        for key, value in cond.items():
            if key not in features:
                return False
            feat_val = features[key]
            if isinstance(value, dict):
                min_val = value.get("min", float("-inf"))
                max_val = value.get("max", float("inf"))
                if not (min_val <= feat_val <= max_val):
                    return False
            elif feat_val != value:
                return False
        return True

    def _update_model_stats(self, outcome: dict):
        self.model_stats["total_outcomes"] = len(self.trade_outcomes)

        wins = [o for o in self.trade_outcomes if o["is_win"]]
        losses = [o for o in self.trade_outcomes if not o["is_win"]]

        if wins:
            self.model_stats["avg_win_pct"] = sum(o["pnl_pct"] for o in wins) / len(wins)
        if losses:
            self.model_stats["avg_loss_pct"] = sum(o["pnl_pct"] for o in losses) / len(losses)

        if self.patterns:
            best = max(self.patterns, key=lambda p: p.success_rate if p.occurrences >= 3 else 0)
            if best.occurrences >= 3:
                self.model_stats["best_pattern"] = best.name

        recent = self.trade_outcomes[-20:]
        if recent:
            recent_wr = sum(1 for o in recent if o["is_win"]) / len(recent) * 100
            self.model_stats["accuracy_trend"].append(round(recent_wr, 1))
            if len(self.model_stats["accuracy_trend"]) > 50:
                self.model_stats["accuracy_trend"] = self.model_stats["accuracy_trend"][-50:]

    def get_accuracy_report(self) -> str:
        total = self.model_stats["total_outcomes"]
        if total == 0:
            return "📊 No learning data yet."

        wins = sum(1 for o in self.trade_outcomes if o["is_win"])
        wr = (wins / total) * 100 if total > 0 else 0

        trend = self.model_stats.get("accuracy_trend", [])
        trend_str = " → ".join(f"{t:.0f}%" for t in trend[-5:]) if trend else "N/A"

        coin_types = {}
        for o in self.trade_outcomes:
            ct = o.get("coin_type", "unknown")
            if ct not in coin_types:
                coin_types[ct] = {"wins": 0, "losses": 0}
            if o["is_win"]:
                coin_types[ct]["wins"] += 1
            else:
                coin_types[ct]["losses"] += 1

        type_lines = []
        for ct, data in sorted(coin_types.items()):
            t = data["wins"] + data["losses"]
            wr_ct = (data["wins"] / t * 100) if t > 0 else 0
            type_lines.append(f"  {ct}: {wr_ct:.0f}% ({t} trades)")

        return (
            f"📊 <b>Learning Report</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Total Trades: {total}\n"
            f"Win Rate: {wr:.1f}% ({wins}W / {total - wins}L)\n"
            f"Avg Win: +{self.model_stats['avg_win_pct']:.1f}%\n"
            f"Avg Loss: {self.model_stats['avg_loss_pct']:.1f}%\n"
            f"Patterns: {len(self.patterns)}\n"
            f"Trend: {trend_str}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<b>By Coin Type:</b>\n" + "\n".join(type_lines)
        )

    def cleanup_old_data(self, max_age_days: int = 30):
        cutoff = time.time() - (max_age_days * 86400)
        self.trade_outcomes = [
            o for o in self.trade_outcomes
            if o.get("timestamp", 0) > cutoff
        ]
        self._save_data()
