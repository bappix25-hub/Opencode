import logging
import time
import json
import os
from typing import Optional
from config import config
from market_monitor import MarketMonitor
from liquidity_analyzer import liquidity_analyzer
from token_blacklist import TokenBlacklist
from mcap100k_predictor import mcap100k_predictor, SIGNAL_WINDOW_SECONDS

logger = logging.getLogger("pump")

COPYCAT_NAMES = {
    "trump", "moon", "tiktok", "tesla", "elon", "musk", "pepe", "doge", "shib",
    "bonk", "wif", "bome", "jup", "jto", "ray", "orca", "usa", "usa250",
    "gork", "grok", "chatgpt", "openai", "apple", "google", "meta", "amazon",
    "bitcoin", "btc", "eth", "solana", "sol",
}

SIGNAL_COMBO_BONUS = {
    frozenset(["vol_spike", "volume_surge", "high_vol_liq"]): 0.15,
    frozenset(["vol_spike", "volume_surge", "high_vol_liq", "all_buying"]): 0.20,
    frozenset(["vol_spike", "volume_surge", "high_vol_liq", "all_buying", "active_wallets"]): 0.25,
    frozenset(["vol_spike", "volume_surge", "high_vol_liq", "active_wallets"]): 0.18,
    frozenset(["vol_spike", "high_vol_liq", "all_buying"]): 0.12,
}


class AdaptiveScorer:
    def __init__(self):
        self.signal_weights = {
            "vol_spike": 0.35,
            "early_momentum": 0.20,
            "volume_surge": 0.30,
            "liq_change": 0.15,
            "high_vol_liq": 0.20,
            "all_buying": 0.25,
            "strong_buying": 0.15,
            "near_migration": 0.10,
            "building": 0.10,
            "strong_demand": 0.10,
            "active_wallets": 0.15,
        }
        self.min_quality = {
            "min_liquidity": 5000,
            "min_wallets_5m": 10,
            "min_wallets_1h": 15,
            "min_fdv": 3000,
            "min_score": 0.60,
        }
        self.last_learn_time = 0
        self.blacklist = TokenBlacklist()
        self.blacklist.learn_from_signals()
        bl_stats = self.blacklist.get_blacklist_stats()
        logger.info(
            f"Blacklist loaded: {bl_stats['total_addresses']} addresses, "
            f"{bl_stats['total_symbol_patterns']} symbol patterns"
        )
        self._learn_from_signals()

    def _learn_from_signals(self):
        try:
            signals_file = os.path.join(os.path.dirname(__file__), "data", "signals.json")
            if not os.path.exists(signals_file):
                return
            with open(signals_file) as f:
                signals = json.load(f)
            if len(signals) < 20:
                return

            wins = [s for s in signals if s.get("final_pnl_pct", 0) >= 0]
            losses = [s for s in signals if s.get("final_pnl_pct", 0) < -10]
            if not wins or not losses:
                return

            win_liq = [s.get("liquidity", 0) for s in wins if s.get("liquidity", 0) > 0]
            loss_liq = [s.get("liquidity", 0) for s in losses if s.get("liquidity", 0) > 0]

            if win_liq and loss_liq:
                avg_win_liq = sum(win_liq) / len(win_liq)
                avg_loss_liq = sum(loss_liq) / len(loss_liq)
                if avg_loss_liq > 0:
                    liq_ratio = avg_win_liq / avg_loss_liq
                    if liq_ratio > 1.2:
                        self.min_quality["min_liquidity"] = max(
                            self.min_quality["min_liquidity"],
                            int(avg_win_liq * 0.5)
                        )

            win_pumps = [s.get("best_profit_pct", 0) for s in wins if s.get("best_profit_pct", 0) > 0]
            if win_pumps:
                avg_pump = sum(win_pumps) / len(win_pumps)
                if avg_pump > 50:
                    self.signal_weights["vol_spike"] = min(0.45, self.signal_weights["vol_spike"] + 0.05)
                    self.signal_weights["volume_surge"] = min(0.35, self.signal_weights["volume_surge"] + 0.05)

            self.last_learn_time = time.time()
            logger.info(
                f"Adaptive scorer learned: {len(wins)}W/{len(losses)}L | "
                f"Min liq: ${self.min_quality['min_liquidity']} | "
                f"Vol spike weight: {self.signal_weights['vol_spike']:.2f}"
            )
        except Exception as e:
            logger.debug(f"Adaptive scorer learn error: {e}")

    def get_weight(self, signal_name: str) -> float:
        return self.signal_weights.get(signal_name, 0.1)

    def get_min_quality(self, key: str) -> float:
        return self.min_quality.get(key, 0)

    def maybe_relearn(self):
        if time.time() - self.last_learn_time > 600:
            self._learn_from_signals()


class PumpDetector:
    def __init__(self, monitor: MarketMonitor):
        self.monitor = monitor
        self.history: dict = {}
        self.recently_detected: dict = {}
        self.token_prices: dict = {}
        self.scorer = AdaptiveScorer()
        self.scan_stats = {
            "scanned": 0, "filtered": 0, "detected": 0, "skip_reasons": {},
        }

    def _is_copycat(self, symbol: str) -> bool:
        s = symbol.lower().replace("$", "").replace("_", "").replace("-", "")
        for name in COPYCAT_NAMES:
            if s == name or s.startswith(name) or s.endswith(name):
                return True
        return False

    def _track_price(self, address: str, price: float) -> dict:
        if address not in self.token_prices:
            self.token_prices[address] = {"first_seen": time.time(), "prices": []}
        entry = self.token_prices[address]
        entry["prices"].append((time.time(), price))
        entry["prices"] = [(t, p) for t, p in entry["prices"] if time.time() - t < 300]
        if len(entry["prices"]) < 2:
            return {"price_change_5m": 0, "is_pumping": False}
        old_price = entry["prices"][0][1]
        pct = ((price - old_price) / old_price * 100) if old_price > 0 else 0
        return {"price_change_5m": pct, "is_pumping": pct > 30}

    def detect(self, metrics: dict) -> Optional[dict]:
        if not metrics:
            return None

        self.scan_stats["scanned"] += 1
        address = metrics["address"]
        symbol = metrics.get("symbol", "???")

        self.scorer.maybe_relearn()

        now = time.time()
        if address in self.recently_detected:
            if now - self.recently_detected[address] < 1800:
                return None
        self.recently_detected[address] = now

        if len(self.recently_detected) > 500:
            cutoff = now - 3600
            self.recently_detected = {
                k: v for k, v in self.recently_detected.items() if v > cutoff
            }

        signals = []
        score = 0.0

        is_blacklisted, bl_reason = self.scorer.blacklist.is_blacklisted(address, symbol)
        if is_blacklisted:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["blacklisted"] = (
                self.scan_stats["skip_reasons"].get("blacklisted", 0) + 1
            )
            logger.info(f"🚫 BLACKLISTED: {symbol} - {bl_reason}")
            return None

        min_fdv = self.scorer.get_min_quality("min_fdv")
        if metrics.get("fdv", 0) < min_fdv:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_fdv"] = (
                self.scan_stats["skip_reasons"].get("low_fdv", 0) + 1
            )
            return None

        if metrics.get("fdv", 0) > config.max_mcap_for_trade:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["high_fdv"] = (
                self.scan_stats["skip_reasons"].get("high_fdv", 0) + 1
            )
            return None

        if self._is_copycat(symbol):
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["copycat"] = (
                self.scan_stats["skip_reasons"].get("copycat", 0) + 1
            )
            return None

        txns = metrics.get("transactions", {})
        m5 = txns.get("m5", {})
        h1 = txns.get("h1", {})
        buyers_5m = m5.get("buyers", 0)
        sellers_5m = m5.get("sellers", 0)
        buyers_1h = h1.get("buyers", 0)
        sellers_1h = h1.get("sellers", 0)
        unique_5m = buyers_5m + sellers_5m
        unique_1h = buyers_1h + sellers_1h

        min_wallets = int(self.scorer.get_min_quality("min_wallets_5m"))
        if unique_5m < min_wallets:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_wallets"] = (
                self.scan_stats["skip_reasons"].get("low_wallets", 0) + 1
            )
            return None

        min_wallets_1h = int(self.scorer.get_min_quality("min_wallets_1h"))
        if unique_1h > 0 and unique_1h < min_wallets_1h:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_wallets_1h"] = (
                self.scan_stats["skip_reasons"].get("low_wallets_1h", 0) + 1
            )
            return None

        price_info = self._track_price(address, metrics["price_usd"])
        if price_info["is_pumping"]:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["late_signal"] = (
                self.scan_stats["skip_reasons"].get("late_signal", 0) + 1
            )
            logger.info(
                f"🚫 LATE SIGNAL: {symbol} already pumped {price_info['price_change_5m']:.0f}%"
            )
            return None

        age = metrics.get("age_seconds", 0)
        if age > SIGNAL_WINDOW_SECONDS:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["too_old"] = (
                self.scan_stats["skip_reasons"].get("too_old", 0) + 1
            )
            return None

        min_liq = self.scorer.get_min_quality("min_liquidity")
        if metrics.get("liquidity", 0) < min_liq:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_liquidity"] = (
                self.scan_stats["skip_reasons"].get("low_liquidity", 0) + 1
            )
            return None

        if metrics.get("fdv", 0) < 5000 and metrics.get("liquidity", 0) < 100:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["too_small"] = (
                self.scan_stats["skip_reasons"].get("too_small", 0) + 1
            )
            return None

        lp_count = metrics.get("lp_count", 0)
        if lp_count > 0 and lp_count <= 1:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["single_lp"] = (
                self.scan_stats["skip_reasons"].get("single_lp", 0) + 1
            )
            logger.info(f"🚫 SINGLE LP {symbol}: LP Count = {lp_count}")
            return None

        prob_100k = mcap100k_predictor.predict_100k_probability(metrics)
        if prob_100k < 0.25:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_100k_prob"] = (
                self.scan_stats["skip_reasons"].get("low_100k_prob", 0) + 1
            )
            logger.info(f"🚫 LOW $100K PROB {symbol}: {prob_100k:.0%}")
            return None

        risk_score, risk_warnings, good_signs, risk_details = liquidity_analyzer.analyze(
            metrics
        )
        if risk_score > 0:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["high_risk"] = (
                self.scan_stats["skip_reasons"].get("high_risk", 0) + 1
            )
            logger.info(
                f"🚫 HIGH RISK {symbol}: Risk {risk_score:.2f} - {risk_warnings}"
            )
            return None

        vol_mult = config.volume_spike_multiplier
        if vol_mult > 0 and metrics["volume_5m"] > 100:
            avg_vol = metrics["volume_1h"] / 12 if metrics["volume_1h"] > 0 else 0
            if avg_vol > 10 and metrics["volume_5m"] > avg_vol * vol_mult:
                spike = metrics["volume_5m"] / avg_vol
                signals.append(f"Vol Spike: {spike:.1f}x")
                score += min(spike / vol_mult, 1.0) * self.scorer.get_weight("vol_spike")

        price_change = price_info["price_change_5m"]
        if 5 < price_change < 30:
            signals.append(f"Early Momentum +{price_change:.0f}%")
            score += self.scorer.get_weight("early_momentum")
        elif price_change <= 5 and metrics["volume_5m"] > 1000:
            signals.append("Volume Surge (pre-pump)")
            score += self.scorer.get_weight("volume_surge")

        liq_thresh = config.liquidity_change_threshold
        if liq_thresh > 0 and metrics["liquidity_change"] > liq_thresh:
            signals.append(f"Liq +{metrics['liquidity_change']:.0f}%")
            score += min(
                metrics["liquidity_change"] / (liq_thresh * 2), 1.0
            ) * self.scorer.get_weight("liq_change")

        if metrics["volume_5m"] > 5000:
            vol_liq_ratio = metrics["volume_5m"] / max(metrics["liquidity"], 1)
            if vol_liq_ratio > 0.3:
                signals.append(f"High Vol/Liq {vol_liq_ratio:.1f}x")
                score += self.scorer.get_weight("high_vol_liq")

        if buyers_5m >= 5 and sellers_5m == 0:
            signals.append(f"All Buying ({buyers_5m}B/0S)")
            score += self.scorer.get_weight("all_buying")
        elif (
            buyers_5m >= 3 and sellers_5m <= 1 and buyers_5m > sellers_5m * 2
        ):
            signals.append(f"Strong Buying ({buyers_5m}B/{sellers_5m}S)")
            score += self.scorer.get_weight("strong_buying")

        progress = metrics.get("bonding_curve_progress", 0)
        sol_raised = metrics.get("sol_raised", 0)
        if progress >= 90 and not metrics.get("complete", False):
            signals.append(f"Near migration ({progress:.0f}%)")
            score += self.scorer.get_weight("near_migration")
        elif progress >= 70 and not metrics.get("complete", False):
            signals.append(f"Building ({progress:.0f}%)")
            score += self.scorer.get_weight("building")

        if sol_raised > 50 and not metrics.get("complete", False):
            signals.append(f"Strong demand ({sol_raised:.0f} SOL)")
            score += self.scorer.get_weight("strong_demand")

        if unique_5m >= 50:
            signals.append(f"Active ({unique_5m} wallets)")
            score += self.scorer.get_weight("active_wallets")
        elif unique_5m >= 20:
            signals.append(f"{unique_5m} wallets trading")
            score += self.scorer.get_weight("active_wallets") * 0.5

        has_vol_spike = any("Vol Spike" in s for s in signals)
        if not has_vol_spike:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["no_vol_spike"] = (
                self.scan_stats["skip_reasons"].get("no_vol_spike", 0) + 1
            )
            return None

        signal_types = set()
        for s in signals:
            sl = s.lower()
            if "vol spike" in sl:
                signal_types.add("vol_spike")
            if "volume surge" in sl or "pre-pump" in sl:
                signal_types.add("volume_surge")
            if "high vol/liq" in sl:
                signal_types.add("high_vol_liq")
            if "all buying" in sl or "strong buying" in sl:
                signal_types.add("all_buying")
            if "active" in sl or "wallets trading" in sl:
                signal_types.add("active_wallets")
            if "early momentum" in sl:
                signal_types.add("early_momentum")
            if "liq +" in sl:
                signal_types.add("liq_change")
            if "near migration" in sl:
                signal_types.add("near_migration")
            if "building" in sl:
                signal_types.add("building")
            if "strong demand" in sl:
                signal_types.add("strong_demand")

        has_old_style = ("near_migration" in signal_types or "building" in signal_types)
        has_quality = ("vol_spike" in signal_types and
                       ("volume_surge" in signal_types or "high_vol_liq" in signal_types))
        if has_old_style and not has_quality:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["old_style"] = (
                self.scan_stats["skip_reasons"].get("old_style", 0) + 1
            )
            logger.info(f"🚫 OLD STYLE: {symbol} - migration signal without volume quality")
            return None

        for combo, bonus in SIGNAL_COMBO_BONUS.items():
            if combo.issubset(signal_types):
                score += bonus
                signals.append(f"Combo bonus +{bonus:.0%}")
                break

        min_score = self.scorer.get_min_quality("min_score")
        if score < min_score or len(signals) < 2:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["low_score"] = (
                self.scan_stats["skip_reasons"].get("low_score", 0) + 1
            )
            return None

        is_good_time, timing_msg = mcap100k_predictor.is_good_timing()
        if not is_good_time and len(mcap100k_predictor.resolved_tokens) > 20:
            self.scan_stats["filtered"] += 1
            self.scan_stats["skip_reasons"]["bad_timing"] = (
                self.scan_stats["skip_reasons"].get("bad_timing", 0) + 1
            )
            logger.info(f"🚫 BAD TIMING {symbol}: {timing_msg}")
            return None

        mcap100k_predictor.register_new_token(symbol, address, metrics)

        score = min(score, 1.0)
        self.scan_stats["detected"] += 1

        if risk_warnings:
            signals.extend([f"WARN: {w}" for w in risk_warnings[:2]])

        if good_signs:
            signals.extend([f"OK: {g}" for g in good_signs[:2]])

        logger.info(
            f"PUMP DETECTED: {symbol} | "
            f"Score: {score:.2f} | Risk: {risk_score:.2f} | "
            f"Signals: {', '.join(signals)} | "
            f"Price: ${metrics['price_usd']:.8f} | "
            f"Vol5m: ${metrics['volume_5m']:.0f} | "
            f"Liq: ${metrics['liquidity']:.0f} | "
            f"Wallets: {unique_5m}"
        )

        return {
            "address": address,
            "symbol": symbol,
            "name": metrics["name"],
            "price_usd": metrics["price_usd"],
            "score": score,
            "signals": signals,
            "metrics": metrics,
            "risk_score": risk_score,
            "risk_warnings": risk_warnings,
            "good_signs": good_signs,
            "prob_100k": prob_100k,
        }
