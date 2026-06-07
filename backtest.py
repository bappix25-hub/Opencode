import asyncio
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from dex_client import DexScreenerClient
from helius_client import HeliusClient
from learner import score_coin, get_launch_age, extract_pattern, learn_pump_with_launch, learn_early_pump
from config import config
from utils import format_number

logger = logging.getLogger("backtest")

REPORTS_DIR = "./backtest_reports"
MAX_REPORTS = 10
DEX_DELAY = 1.2
HELIUS_DELAY = 0.15
MAX_RETRIES = 3

class BacktestEngine:
    def __init__(self, session, dex: DexScreenerClient, helius: HeliusClient, send_msg_func=None):
        self.session = session
        self.dex = dex
        self.helius = helius
        self.send_msg = send_msg_func
        self._failure_streak = 0
        self._circuit_breaker_until = None

    async def _safe_sleep(self, delay: float):
        await asyncio.sleep(delay)

    async def _check_circuit_breaker(self):
        if self._circuit_breaker_until and datetime.now(timezone.utc).timestamp() < self._circuit_breaker_until:
            wait = self._circuit_breaker_until - datetime.now(timezone.utc).timestamp()
            logger.warning(f"Circuit breaker active, waiting {wait:.0f}s")
            await asyncio.sleep(wait)
            self._circuit_breaker_until = None

    async def _fetch_with_protection(self, fetch_func, *args, delay=DEX_DELAY, **kwargs):
        await self._check_circuit_breaker()
        for attempt in range(MAX_RETRIES):
            try:
                await self._safe_sleep(delay)
                result = await fetch_func(*args, **kwargs)
                self._failure_streak = 0
                return result
            except Exception as e:
                self._failure_streak += 1
                logger.warning(f"Fetch error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if self._failure_streak >= 5:
                    self._circuit_breaker_until = datetime.now(timezone.utc).timestamp() + 300
                    logger.error("Circuit breaker triggered - 5min pause")
                    await self._check_circuit_breaker()
                await asyncio.sleep(delay * (2 ** attempt))
        return None

    async def collect_tokens(self, days: int = 30, max_tokens: int = 300) -> list:
        logger.info(f"📊 Collecting tokens from last {days} days...")
        all_addrs = set()

        new_tokens = await self._fetch_with_protection(self.dex.fetch_new_solana_pairs)
        if new_tokens:
            for t in new_tokens:
                addr = t.get("tokenAddress") or t.get("address")
                if addr:
                    all_addrs.add(addr)
            logger.info(f"Found {len(new_tokens)} from new_pairs")

        boosted = await self._fetch_with_protection(self.dex.fetch_boosted_pairs)
        if boosted:
            for t in boosted:
                addr = t.get("tokenAddress") or t.get("address")
                if addr:
                    all_addrs.add(addr)
            logger.info(f"Found {len(boosted)} from boosted")

        # Also fetch historical top pairs (for pumped tokens that may not be in new/boosted)
        try:
            top_pairs = await self._fetch_with_protection(self.dex.fetch_top_pairs, 100)
            if top_pairs:
                for pair in top_pairs:
                    addr = pair.get("tokenAddress") or pair.get("address")
                    if addr:
                        all_addrs.add(addr)
                logger.info(f"Found {len(top_pairs)} from top_pairs (historical)")
        except Exception as e:
            logger.debug(f"fetch_top_pairs failed: {e}")

        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        valid_tokens = []

        addrs_list = list(all_addrs)[:max_tokens]
        for i, addr in enumerate(addrs_list):
            if i % 25 == 0:
                logger.info(f"Progress: {i}/{len(addrs_list)} tokens checked")

            pair = await self._fetch_with_protection(self.dex.fetch_pair_data, addr)
            if not pair:
                continue

            created_at = pair.get("pairCreatedAt")
            if not created_at:
                continue
            try:
                created_ms = int(created_at)
            except (ValueError, TypeError):
                continue

            if created_ms < cutoff_ms:
                continue

            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            mcap = float(pair.get("fdv", 0) or 0)
            if liquidity < 500 or mcap < 1000:
                continue

            valid_tokens.append({
                "address": addr,
                "pair": pair,
                "name": pair.get("baseToken", {}).get("name", "Unknown"),
                "symbol": pair.get("baseToken", {}).get("symbol", "???"),
            })

        logger.info(f"✅ Collected {len(valid_tokens)} valid tokens")
        return valid_tokens

    def identify_pump(self, pair: dict) -> tuple:
        try:
            h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
            h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
            h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
            best = max(h1, h6, h24)
            multiplier = 1 + best / 100
            is_pump = multiplier >= 3.0
            return is_pump, round(multiplier, 2)
        except Exception:
            return False, 0.0

    def identify_5x_pump(self, pair: dict) -> tuple:
        try:
            h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
            h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
            h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
            best = max(h1, h6, h24)
            multiplier = 1 + best / 100
            is_5x = multiplier >= 5.0
            return is_5x, round(multiplier, 2)
        except Exception:
            return False, 0.0

    async def evaluate_token(self, token_info: dict) -> dict:
        addr = token_info["address"]
        pair = token_info["pair"]
        name = token_info["name"]
        symbol = token_info["symbol"]

        is_pump, actual_multi = self.identify_pump(pair)
        is_5x, _ = self.identify_5x_pump(pair)
        age = get_launch_age(pair) or 0

        ai_score = 0.0
        reason = ""
        try:
            ai_score, reason = score_coin(pair, {"name": name, "symbol": symbol}, age)
        except Exception as e:
            logger.warning(f"score_coin error for {symbol}: {e}")

        from config import config as live_config
        threshold = live_config.ai_threshold
        predicted_pump = ai_score >= threshold
        threshold_used = threshold

        if is_pump and predicted_pump:
            verdict = "TP"
        elif is_pump and not predicted_pump:
            verdict = "FN"
        elif not is_pump and predicted_pump:
            verdict = "FP"
        else:
            verdict = "TN"

        return {
            "address": addr,
            "name": name,
            "symbol": symbol,
            "actual_pump": is_pump,
            "actual_5x": is_5x,
            "actual_multiplier": actual_multi,
            "ai_score": ai_score,
            "ai_threshold": threshold_used,
            "predicted_pump": predicted_pump,
            "verdict": verdict,
            "age_seconds": age,
            "reason": reason,
            "mcap": float(pair.get("fdv", 0) or 0),
            "liquidity": float(pair.get("liquidity", {}).get("usd", 0) or 0),
            "volume_h1": float(pair.get("volume", {}).get("h1", 0) or 0),
        }

    def calculate_metrics(self, results: list) -> dict:
        tp = sum(1 for r in results if r["verdict"] == "TP")
        fp = sum(1 for r in results if r["verdict"] == "FP")
        tn = sum(1 for r in results if r["verdict"] == "TN")
        fn = sum(1 for r in results if r["verdict"] == "FN")
        total = len(results)
        actual_pumps = sum(1 for r in results if r["actual_pump"])
        actual_5x = sum(1 for r in results if r.get("actual_5x", False))
        signals_sent = tp + fp

        precision = tp / signals_sent if signals_sent > 0 else 0
        recall = tp / actual_pumps if actual_pumps > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = (tp + tn) / total if total > 0 else 0
        win_rate = precision
        avg_multiplier = (
            sum(r["actual_multiplier"] for r in results if r["verdict"] == "TP") / tp
            if tp > 0 else 0
        )
        tp_5x = sum(1 for r in results if r["verdict"] == "TP" and r.get("actual_5x", False))
        five_x_precision = tp_5x / signals_sent if signals_sent > 0 else 0
        five_x_recall = tp_5x / actual_5x if actual_5x > 0 else 0

        hour_stats = {}
        for r in results:
            if r["verdict"] in ("TP", "FP"):
                h = r["reason"][:50] if r["reason"] else "unknown"
                hour_stats[h] = hour_stats.get(h, 0) + 1

        best_hours = {}
        for r in results:
            if r["verdict"] in ("TP", "FP", "TN", "FN"):
                if "age_seconds" not in r or r["age_seconds"] <= 0:
                    continue
                h = str(datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - r["age_seconds"], tz=timezone.utc).hour)
                if h not in best_hours:
                    best_hours[h] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
                best_hours[h][r["verdict"].lower()] = best_hours[h].get(r["verdict"].lower(), 0) + 1

        hour_success = {}
        for h, stats in best_hours.items():
            total_h = sum(stats.values())
            if total_h > 0:
                hour_success[h] = round(stats["tp"] / total_h, 3)

        return {
            "total_tokens": total,
            "actual_pumps": actual_pumps,
            "actual_5x": actual_5x,
            "dumps": total - actual_pumps,
            "signals_sent": signals_sent,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "tp_5x": tp_5x,
            "precision": round(precision * 100, 1),
            "recall": round(recall * 100, 1),
            "f1_score": round(f1, 3),
            "accuracy": round(accuracy * 100, 1),
            "win_rate": round(win_rate * 100, 1),
            "avg_multiplier": round(avg_multiplier, 2),
            "five_x_precision": round(five_x_precision * 100, 1),
            "five_x_recall": round(five_x_recall * 100, 1),
            "hour_success_rate": hour_success,
        }

    def _format_telegram_report(self, metrics: dict, days: int) -> str:
        verdict_emoji = "✅" if metrics["win_rate"] >= 50 else "⚠️"
        verdict_text = "Model profitable — proceed" if metrics["win_rate"] >= 50 else "Model needs improvement"

        top_hours = sorted(metrics["hour_success_rate"].items(), key=lambda x: x[1], reverse=True)[:5]
        hours_text = ""
        for i, (h, rate) in enumerate(top_hours, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
            hours_text += f"{medal} {h}:00 UTC — {int(rate*100)}% success\n"

        if not hours_text:
            hours_text = "Not enough data\n"

        return (
            f"🧪 <b>Backtest Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Period: <b>{days} days</b>\n"
            f"📊 Total tokens: <b>{metrics['total_tokens']}</b>\n"
            f"🚀 3x+ pumps: <b>{metrics['actual_pumps']} ({round(metrics['actual_pumps']/max(metrics['total_tokens'],1)*100, 1)}%)</b>\n"
            f"🌟 5x+ pumps: <b>{metrics.get('actual_5x', 0)} ({round(metrics.get('actual_5x', 0)/max(metrics['total_tokens'],1)*100, 1)}%)</b>\n"
            f"📉 Dumps: <b>{metrics['dumps']}</b>\n\n"
            f"<b>AI Performance (3x target):</b>\n"
            f"🎯 Win Rate: <b>{metrics['win_rate']}%</b> (AI কতটুকু সঠিক পাম্প চেনে)\n"
            f"📊 Avg Multiplier: <b>{metrics['avg_multiplier']}x</b>\n"
            f"⚖️ F1 Score: <b>{metrics['f1_score']}</b>\n\n"
            f"<b>AI Performance (5x target):</b>\n"
            f"🌟 5x Win Rate: <b>{metrics.get('five_x_precision', 0)}%</b>\n"
            f"🌟 5x Signals: <b>{metrics.get('tp_5x', 0)}</b>\n\n"
            f"<b>Best Hours (UTC):</b>\n"
            f"{hours_text}\n"
            f"<b>Verdict:</b> {verdict_emoji} <i>{verdict_text}</i>"
        )

    def _save_report_files(self, metrics: dict, results: list, days: int) -> str:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(REPORTS_DIR, f"backtest_{ts}.json")

        report_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period_days": days,
            "metrics": metrics,
            "results": results[:100],
            "total_results": len(results),
        }
        with open(json_path, "w") as f:
            json.dump(report_data, f, indent=2)

        self._cleanup_old_reports()

        summary_path = "backtest_summary.md"
        with open(summary_path, "w") as f:
            f.write(f"# Latest Backtest Summary\n\n")
            f.write(f"**Date:** {report_data['timestamp']}\n")
            f.write(f"**Period:** {days} days\n\n")
            f.write(f"## Metrics\n\n")
            f.write(f"| Metric | Value |\n|--------|-------|\n")
            for k, v in metrics.items():
                if not isinstance(v, dict):
                    f.write(f"| {k} | {v} |\n")
            f.write(f"\n## Verdict\n\n")
            verdict = "✅ Profitable" if metrics["win_rate"] >= 50 else "⚠️ Needs improvement"
            f.write(f"{verdict} (Win rate: {metrics['win_rate']}%)\n")

        logger.info(f"📁 Reports saved: {json_path}, {summary_path}")
        return json_path

    def _cleanup_old_reports(self):
        try:
            files = []
            for f in os.listdir(REPORTS_DIR):
                if f.startswith("backtest_") and f.endswith(".json"):
                    full = os.path.join(REPORTS_DIR, f)
                    files.append((os.path.getmtime(full), full))
            files.sort(reverse=True)
            for _, old_file in files[MAX_REPORTS:]:
                os.remove(old_file)
                logger.info(f"🗑️ Removed old report: {old_file}")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

    async def run(self, days: int = 30, max_tokens: int = 300, progress_callback=None) -> dict:
        logger.info(f"🚀 Starting {days}-day backtest...")
        start_ts = datetime.now(timezone.utc).timestamp()

        if self.send_msg:
            await self.send_msg(
                f"🧪 <b>Backtest শুরু!</b>\n"
                f"📅 {days} দিনের ডেটা কালেক্ট হচ্ছে...\n"
                f"⏱️ আনুমানিক ৩০-৯০ মিনিট লাগবে"
            )

        tokens = await self.collect_tokens(days, max_tokens)
        if not tokens:
            logger.error("No tokens collected")
            return {"error": "No tokens found"}

        results = []
        trained_pumps = 0
        trained_early = 0
        for i, token in enumerate(tokens):
            result = await self.evaluate_token(token)
            results.append(result)

            if result["actual_pump"] and result["actual_multiplier"] >= 3.0:
                try:
                    pair = token["pair"]
                    addr = token["address"]
                    name = token["name"]
                    symbol = token["symbol"]
                    age = get_launch_age(pair) or 0

                    txs = await self.helius.get_launch_transactions(addr)
                    launch_pat = extract_launch_pattern(txs) if txs else None

                    ok, msg = learn_pump_with_launch(
                        {"name": name, "symbol": symbol}, pair,
                        result["actual_multiplier"], launch_pat, addr, manual=False,
                        verified_multiplier=result["actual_multiplier"]
                    )
                    if ok:
                        trained_pumps += 1

                    if age <= 600 and result["actual_multiplier"] >= 2.0:
                        launch_dict = {
                            "buy_count": launch_pat.get("buy_count", 0) if launch_pat else 0,
                            "sell_count": launch_pat.get("sell_count", 0) if launch_pat else 0,
                            "unique_wallets": launch_pat.get("unique_wallets", 0) if launch_pat else 0,
                            "volume": launch_pat.get("volume", 0) if launch_pat else 0,
                        }
                        ok2, msg2 = learn_early_pump(
                            addr, symbol, name, pair, launch_dict,
                            age, result["actual_multiplier"]
                        )
                        if ok2:
                            trained_early += 1
                except Exception as e:
                    logger.debug(f"Train error for {token.get('symbol', '?')}: {e}")

            if (i + 1) % 25 == 0:
                logger.info(f"Evaluated {i+1}/{len(tokens)} (trained: {trained_pumps} pumps, {trained_early} early)")
                if progress_callback:
                    await progress_callback(i + 1, len(tokens))

        metrics = self.calculate_metrics(results)
        metrics["elapsed_seconds"] = round(datetime.now(timezone.utc).timestamp() - start_ts, 1)
        metrics["period_days"] = days
        metrics["trained_pumps"] = trained_pumps
        metrics["trained_early_pumps"] = trained_early

        json_path = self._save_report_files(metrics, results, days)

        if self.send_msg:
            report = self._format_telegram_report(metrics, days)
            report += f"\n\n📚 <b>Training:</b>\n"
            report += f"🚀 Pumps trained: <b>{trained_pumps}</b>\n"
            report += f"🎯 Early pumps trained: <b>{trained_early}</b>"
            await self.send_msg(report)

        logger.info(f"✅ Backtest complete in {metrics['elapsed_seconds']}s")
        logger.info(f"📊 Win rate: {metrics['win_rate']}%, Precision: {metrics['precision']}%, Recall: {metrics['recall']}%")
        logger.info(f"📚 Trained: {trained_pumps} pumps, {trained_early} early pumps")

        return {
            "metrics": metrics,
            "json_path": json_path,
            "results": results,
        }


async def run_backtest_async(session, days: int = 30, max_tokens: int = 300, send_msg_func=None):
    dex = DexScreenerClient(session)
    helius = HeliusClient(session)
    engine = BacktestEngine(session, dex, helius, send_msg_func)
    return await engine.run(days, max_tokens)
import asyncio
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from dex_client import DexScreenerClient
from helius_client import HeliusClient
from learner import (
    score_coin, get_launch_age, extract_pattern, learn_pump_with_launch,
    learn_early_pump, load_data, save_data, _hash_address, is_duplicate,
    _update_model, _mark_trained
)
from config import config
from utils import format_number

logger = logging.getLogger("backtest")

REPORTS_DIR = "./backtest_reports"
MAX_REPORTS = 10
DEX_DELAY = 1.2
HELIUS_DELAY = 0.15
MAX_RETRIES = 3


class BacktestEngine:
    def __init__(self, session, dex: DexScreenerClient, helius: HeliusClient, send_msg_func=None):
        self.session = session
        self.dex = dex
        self.helius = helius
        self.send_msg = send_msg_func
        self._failure_streak = 0
        self._circuit_breaker_until = None

    async def _safe_sleep(self, delay: float):
        await asyncio.sleep(delay)

    async def _check_circuit_breaker(self):
        if self._circuit_breaker_until and datetime.now(timezone.utc).timestamp() < self._circuit_breaker_until:
            wait = self._circuit_breaker_until - datetime.now(timezone.utc).timestamp()
            logger.warning(f"Circuit breaker active, waiting {wait:.0f}s")
            await asyncio.sleep(wait)
            self._circuit_breaker_until = None

    async def _fetch_with_protection(self, fetch_func, *args, delay=DEX_DELAY, **kwargs):
        await self._check_circuit_breaker()
        for attempt in range(MAX_RETRIES):
            try:
                await self._safe_sleep(delay)
                result = await fetch_func(*args, **kwargs)
                self._failure_streak = 0
                return result
            except Exception as e:
                self._failure_streak += 1
                logger.warning(f"Fetch error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if self._failure_streak >= 5:
                    self._circuit_breaker_until = datetime.now(timezone.utc).timestamp() + 300
                    logger.error("Circuit breaker triggered - 5min pause")
                    await self._check_circuit_breaker()
                await asyncio.sleep(delay * (2 ** attempt))
        return None

    async def collect_tokens(self, days: int = 30, max_tokens: int = 300) -> list:
        logger.info(f"📊 Collecting tokens from last {days} days...")
        all_addrs = set()

        new_tokens = await self._fetch_with_protection(self.dex.fetch_new_solana_pairs)
        if new_tokens:
            for t in new_tokens:
                addr = t.get("tokenAddress") or t.get("address")
                if addr:
                    all_addrs.add(addr)
            logger.info(f"Found {len(new_tokens)} from new_pairs")

        boosted = await self._fetch_with_protection(self.dex.fetch_boosted_pairs)
        if boosted:
            for t in boosted:
                addr = t.get("tokenAddress") or t.get("address")
                if addr:
                    all_addrs.add(addr)
            logger.info(f"Found {len(boosted)} from boosted")

        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        valid_tokens = []
        addrs_list = list(all_addrs)[:max_tokens]

        for i, addr in enumerate(addrs_list):
            if i % 25 == 0:
                logger.info(f"Progress: {i}/{len(addrs_list)} tokens checked")

            pair = await self._fetch_with_protection(self.dex.fetch_pair_data, addr)
            if not pair:
                continue

            created_at = pair.get("pairCreatedAt")
            if not created_at:
                continue

            try:
                created_ms = int(created_at)
            except (ValueError, TypeError):
                continue

            if created_ms < cutoff_ms:
                continue

            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            mcap = float(pair.get("fdv", 0) or 0)
            if liquidity < 500 or mcap < 1000:
                continue

            valid_tokens.append({
                "address": addr,
                "pair": pair,
                "name": pair.get("baseToken", {}).get("name", "Unknown"),
                "symbol": pair.get("baseToken", {}).get("symbol", "???"),
            })

        logger.info(f"✅ Collected {len(valid_tokens)} valid tokens")
        return valid_tokens

    def identify_pump(self, pair: dict) -> tuple:
        try:
            h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
            h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
            h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
            best = max(h1, h6, h24)
            multiplier = 1 + best / 100
            is_pump = multiplier >= 3.0
            return is_pump, round(multiplier, 2)
        except Exception:
            return False, 0.0

    def identify_5x_pump(self, pair: dict) -> tuple:
        try:
            h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
            h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
            h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
            best = max(h1, h6, h24)
            multiplier = 1 + best / 100
            is_5x = multiplier >= 5.0
            return is_5x, round(multiplier, 2)
        except Exception:
            return False, 0.0

    async def evaluate_token(self, token_info: dict) -> dict:
        addr = token_info["address"]
        pair = token_info["pair"]
        name = token_info["name"]
        symbol = token_info["symbol"]

        is_pump, actual_multi = self.identify_pump(pair)
        is_5x, _ = self.identify_5x_pump(pair)
        age = get_launch_age(pair) or 0

        ai_score = 0.0
        reason = ""
        try:
            ai_score, reason = score_coin(pair, {"name": name, "symbol": symbol}, age)
        except Exception as e:
            logger.warning(f"score_coin error for {symbol}: {e}")

        from config import config as live_config
        threshold = live_config.ai_threshold
        predicted_pump = ai_score >= threshold
        threshold_used = threshold

        if is_pump and predicted_pump:
            verdict = "TP"
        elif is_pump and not predicted_pump:
            verdict = "FN"
        elif not is_pump and predicted_pump:
            verdict = "FP"
        else:
            verdict = "TN"

        return {
            "address": addr,
            "name": name,
            "symbol": symbol,
            "actual_pump": is_pump,
            "actual_5x": is_5x,
            "actual_multiplier": actual_multi,
            "ai_score": ai_score,
            "ai_threshold": threshold_used,
            "predicted_pump": predicted_pump,
            "verdict": verdict,
            "age_seconds": age,
            "reason": reason,
            "mcap": float(pair.get("fdv", 0) or 0),
            "liquidity": float(pair.get("liquidity", {}).get("usd", 0) or 0),
            "volume_h1": float(pair.get("volume", {}).get("h1", 0) or 0),
        }

    def calculate_metrics(self, results: list) -> dict:
        tp = sum(1 for r in results if r["verdict"] == "TP")
        fp = sum(1 for r in results if r["verdict"] == "FP")
        tn = sum(1 for r in results if r["verdict"] == "TN")
        fn = sum(1 for r in results if r["verdict"] == "FN")
        total = len(results)
        actual_pumps = sum(1 for r in results if r["actual_pump"])
        actual_5x = sum(1 for r in results if r.get("actual_5x", False))
        signals_sent = tp + fp

        precision = tp / signals_sent if signals_sent > 0 else 0
        recall = tp / actual_pumps if actual_pumps > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = (tp + tn) / total if total > 0 else 0
        win_rate = precision

        avg_multiplier = (
            sum(r["actual_multiplier"] for r in results if r["verdict"] == "TP") / tp
            if tp > 0 else 0
        )

        tp_5x = sum(1 for r in results if r["verdict"] == "TP" and r.get("actual_5x", False))
        five_x_precision = tp_5x / signals_sent if signals_sent > 0 else 0
        five_x_recall = tp_5x / actual_5x if actual_5x > 0 else 0

        best_hours = {}
        for r in results:
            if r.get("age_seconds", 0) > 0:
                h = str(datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() - r["age_seconds"],
                    tz=timezone.utc
                ).hour)
            else:
                h = str(datetime.now(timezone.utc).hour)

            if h not in best_hours:
                best_hours[h] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
            best_hours[h][r["verdict"].lower()] = best_hours[h].get(r["verdict"].lower(), 0) + 1

        hour_success = {}
        for h, stats in best_hours.items():
            total_h = sum(stats.values())
            if total_h > 0:
                hour_success[h] = round(stats["tp"] / total_h, 3)

        return {
            "total_tokens": total,
            "actual_pumps": actual_pumps,
            "actual_5x": actual_5x,
            "dumps": total - actual_pumps,
            "signals_sent": signals_sent,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "tp_5x": tp_5x,
            "precision": round(precision * 100, 1),
            "recall": round(recall * 100, 1),
            "f1_score": round(f1, 3),
            "accuracy": round(accuracy * 100, 1),
            "win_rate": round(win_rate * 100, 1),
            "avg_multiplier": round(avg_multiplier, 2),
            "five_x_precision": round(five_x_precision * 100, 1),
            "five_x_recall": round(five_x_recall * 100, 1),
            "hour_success_rate": hour_success,
        }

    def _force_train_from_results(self, results: list, token_map: dict) -> tuple:
        """
        BUG FIX: Backtest sonno train korar main reason chilo is_duplicate() block.
        Ekhane directly data manipulate kore training kori, duplicate skip na kore.
        Golden pattern promote kori TP result theke.
        """
        trained_pumps = 0
        trained_early = 0
        golden_promoted = 0

        data = load_data()

        for r in results:
            addr = r["address"]
            symbol = r["symbol"]
            name = r["name"]
            multiplier = r["actual_multiplier"]
            age = r.get("age_seconds", 0)
            mcap = r.get("mcap", 0)
            liquidity = r.get("liquidity", 0)
            vol_h1 = r.get("volume_h1", 0)

            token_info = token_map.get(addr, {})
            pair = token_info.get("pair", {})

            # --- PUMP TRAINING (3x+) ---
            if r["actual_pump"] and multiplier >= 3.0:
                h = _hash_address(addr)
                # Force insert even if duplicate in backtest mode
                # (backtest tokens shouldn't block live learning)
                if h not in data.get("trained_addresses", []):
                    try:
                        from learner import extract_pattern as ep
                        pattern = ep(pair, age) if pair else None
                        if pattern is None:
                            # Build synthetic pattern from backtest data
                            vol_liq = vol_h1 / liquidity if liquidity > 0 else 0.3
                            pattern = {
                                "mcap": mcap,
                                "liquidity": liquidity,
                                "volume_h1": vol_h1,
                                "volume_m5": vol_h1 * 0.1,
                                "age_seconds": age,
                                "price_change_5m": (multiplier - 1) * 20,
                                "price_change_1h": (multiplier - 1) * 100,
                                "hour_of_day": datetime.now(timezone.utc).hour,
                                "buys_m5": 5,
                                "sells_m5": 2,
                                "buys_h1": 20,
                                "sells_h1": 5,
                                "vol_liq_ratio": vol_liq,
                                "buy_sell_ratio_m5": 0.7,
                                "buy_sell_ratio_h1": 0.8,
                                "mcap_liq_ratio": mcap / liquidity if liquidity > 0 else 5,
                                "momentum_5m": (multiplier - 1) * 20,
                            }

                        pattern["symbol"] = symbol
                        pattern["name"] = name
                        pattern["address"] = addr
                        pattern["final_multiplier"] = multiplier
                        pattern["manual"] = False
                        pattern["source"] = "backtest"
                        pattern["timestamp"] = datetime.now(timezone.utc).isoformat()

                        data["pump_patterns"].append(pattern)
                        data["pump_patterns"] = data["pump_patterns"][-500:]
                        _mark_trained(data, addr)
                        trained_pumps += 1

                        # Golden pattern promote: 5x+ pumps
                        if multiplier >= 5.0:
                            self._promote_golden_in_data(data, symbol, addr, pattern, multiplier)
                            golden_promoted += 1

                    except Exception as e:
                        logger.debug(f"Force train pump error {symbol}: {e}")

            # --- EARLY PUMP TRAINING (2x+ within 10 min) ---
            if r["actual_pump"] and multiplier >= 2.0 and 0 < age <= 3600:
                # age <= 3600 (1 hour) — relaxed from 600s
                h = _hash_address(addr + "_early")
                if h not in data.get("trained_addresses", []):
                    try:
                        vol_liq = vol_h1 / liquidity if liquidity > 0 else 0.3
                        launch_dict = {
                            "buy_count": int(vol_h1 / max(liquidity * 0.01, 1)) + 5,
                            "sell_count": 3,
                            "unique_wallets": max(5, int(multiplier * 3)),
                            "volume": vol_h1,
                            "buy_sell_ratio": 2.5,
                            "buy_velocity": max(1.0, multiplier),
                            "curve_fill_pct": min(95, multiplier * 20),
                        }
                        ep_pattern = {
                            "mcap": mcap,
                            "liquidity": liquidity,
                            "volume_h1": vol_h1,
                            "volume_m5": vol_h1 * 0.2,
                            "age_seconds": age,
                            "price_change_5m": (multiplier - 1) * 30,
                            "price_change_1h": (multiplier - 1) * 100,
                            "hour_of_day": datetime.now(timezone.utc).hour,
                            "buys_m5": 8,
                            "sells_m5": 3,
                            "buys_h1": 25,
                            "sells_h1": 8,
                            "vol_liq_ratio": vol_liq,
                            "buy_sell_ratio_m5": 0.72,
                            "buy_sell_ratio_h1": 0.75,
                            "mcap_liq_ratio": mcap / liquidity if liquidity > 0 else 5,
                            "momentum_5m": (multiplier - 1) * 30,
                            "symbol": symbol,
                            "name": name,
                            "address": addr + "_early",
                            "final_multiplier": multiplier,
                            "source": "early_pump_backtest",
                            "age_at_signal": age,
                            "launch_data": launch_dict,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

                        data["pump_patterns"].append(ep_pattern)
                        data["pump_patterns"] = data["pump_patterns"][-500:]

                        launch_pattern = {
                            **launch_dict,
                            "symbol": symbol,
                            "address": addr + "_early",
                            "final_multiplier": multiplier,
                            "age_seconds": age,
                            "source": "early_pump_backtest",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        data["launch_patterns"].append(launch_pattern)
                        data["launch_patterns"] = data["launch_patterns"][-500:]

                        _mark_trained(data, addr + "_early")
                        trained_early += 1

                    except Exception as e:
                        logger.debug(f"Force train early pump error {symbol}: {e}")

            # --- DUMP TRAINING ---
            elif not r["actual_pump"] and multiplier < 2.0:
                h = _hash_address(addr + "_dump")
                if h not in data.get("trained_addresses", []):
                    try:
                        dump_pattern = {
                            "mcap": mcap,
                            "liquidity": liquidity,
                            "volume_h1": vol_h1,
                            "volume_m5": vol_h1 * 0.1,
                            "age_seconds": age,
                            "price_change_5m": (multiplier - 1) * 10,
                            "price_change_1h": (multiplier - 1) * 100,
                            "hour_of_day": datetime.now(timezone.utc).hour,
                            "buys_m5": 3,
                            "sells_m5": 5,
                            "buys_h1": 10,
                            "sells_h1": 15,
                            "vol_liq_ratio": vol_h1 / liquidity if liquidity > 0 else 0.1,
                            "buy_sell_ratio_m5": 0.35,
                            "buy_sell_ratio_h1": 0.4,
                            "mcap_liq_ratio": mcap / liquidity if liquidity > 0 else 3,
                            "momentum_5m": (multiplier - 1) * 10,
                            "symbol": symbol,
                            "address": addr + "_dump",
                            "final_multiplier": multiplier,
                            "source": "backtest_dump",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        data["dump_patterns"].append(dump_pattern)
                        data["dump_patterns"] = data["dump_patterns"][-500:]
                        _mark_trained(data, addr + "_dump")
                    except Exception as e:
                        logger.debug(f"Dump train error {symbol}: {e}")

        # Update model with all new patterns
        if trained_pumps > 0 or trained_early > 0:
            _update_model(data)

        save_data(data)
        logger.info(f"✅ Force trained: {trained_pumps} pumps, {trained_early} early, {golden_promoted} golden")
        return trained_pumps, trained_early, golden_promoted

    def _promote_golden_in_data(self, data: dict, symbol: str, addr: str, pattern: dict, multiplier: float):
        """Directly update golden_patterns.json from backtest results."""
        try:
            golden_file = "./golden_patterns.json"
            if os.path.exists(golden_file):
                with open(golden_file, "r") as f:
                    golden = json.load(f)
            else:
                golden = {"patterns": [], "min_count": 5, "min_multiplier": 5.0}

            mcap = pattern.get("mcap", 0)
            liquidity = pattern.get("liquidity", 0)

            found = False
            for gp in golden.get("patterns", []):
                if gp.get("symbol") == symbol:
                    gp["count"] = gp.get("count", 0) + 1
                    gp["max_multiplier"] = max(gp.get("max_multiplier", 0), multiplier)
                    gp["avg_multiplier"] = (gp.get("avg_multiplier", multiplier) + multiplier) / 2
                    found = True
                    break

            if not found:
                golden.setdefault("patterns", []).append({
                    "symbol": symbol,
                    "address": addr,
                    "count": 1,
                    "max_multiplier": multiplier,
                    "avg_multiplier": multiplier,
                    "source": "backtest",
                    "mcap_range": [
                        max(0, mcap * 0.3),
                        mcap * 3.0
                    ] if mcap > 0 else [0, 500000],
                    "liquidity_range": [
                        max(0, liquidity * 0.3),
                        liquidity * 3.0
                    ] if liquidity > 0 else [0, 100000],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            with open(golden_file, "w") as f:
                json.dump(golden, f, indent=2)

        except Exception as e:
            logger.debug(f"Golden promote error: {e}")

    def _format_telegram_report(self, metrics: dict, days: int) -> str:
        verdict_emoji = "✅" if metrics["win_rate"] >= 50 else "⚠️"
        verdict_text = "Model profitable — proceed" if metrics["win_rate"] >= 50 else "Model needs improvement"

        top_hours = sorted(metrics["hour_success_rate"].items(), key=lambda x: x[1], reverse=True)[:5]
        hours_text = ""
        for i, (h, rate) in enumerate(top_hours, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1]
            hours_text += f"{medal} {h}:00 UTC — {int(rate*100)}% success\n"
        if not hours_text:
            hours_text = "Not enough data\n"

        return (
            f"🧪 <b>Backtest Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Period: <b>{days} days</b>\n"
            f"📊 Total tokens: <b>{metrics['total_tokens']}</b>\n"
            f"🚀 3x+ pumps: <b>{metrics['actual_pumps']} ({round(metrics['actual_pumps']/max(metrics['total_tokens'],1)*100, 1)}%)</b>\n"
            f"🌟 5x+ pumps: <b>{metrics.get('actual_5x', 0)} ({round(metrics.get('actual_5x', 0)/max(metrics['total_tokens'],1)*100, 1)}%)</b>\n"
            f"📉 Dumps: <b>{metrics['dumps']}</b>\n\n"
            f"<b>AI Performance (3x target):</b>\n"
            f"🎯 Win Rate: <b>{metrics['win_rate']}%</b> (AI কতটুকু সঠিক পাম্প চেনে)\n"
            f"📊 Avg Multiplier: <b>{metrics['avg_multiplier']}x</b>\n"
            f"⚖️ F1 Score: <b>{metrics['f1_score']}</b>\n\n"
            f"<b>AI Performance (5x target):</b>\n"
            f"🌟 5x Win Rate: <b>{metrics.get('five_x_precision', 0)}%</b>\n"
            f"🌟 5x Signals: <b>{metrics.get('tp_5x', 0)}</b>\n\n"
            f"<b>Best Hours (UTC):</b>\n"
            f"{hours_text}\n"
            f"<b>Verdict:</b> {verdict_emoji} <i>{verdict_text}</i>"
        )

    def _save_report_files(self, metrics: dict, results: list, days: int) -> str:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(REPORTS_DIR, f"backtest_{ts}.json")

        report_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period_days": days,
            "metrics": metrics,
            "results": results[:100],
            "total_results": len(results),
        }

        with open(json_path, "w") as f:
            json.dump(report_data, f, indent=2)

        self._cleanup_old_reports()

        summary_path = "backtest_summary.md"
        with open(summary_path, "w") as f:
            f.write(f"# Latest Backtest Summary\n\n")
            f.write(f"**Date:** {report_data['timestamp']}\n")
            f.write(f"**Period:** {days} days\n\n")
            f.write(f"## Metrics\n\n")
            f.write(f"| Metric | Value |\n|--------|-------|\n")
            for k, v in metrics.items():
                if not isinstance(v, dict):
                    f.write(f"| {k} | {v} |\n")
            f.write(f"\n## Verdict\n\n")
            verdict = "✅ Profitable" if metrics["win_rate"] >= 50 else "⚠️ Needs improvement"
            f.write(f"{verdict} (Win rate: {metrics['win_rate']}%)\n")

        logger.info(f"📁 Reports saved: {json_path}, {summary_path}")
        return json_path

    def _cleanup_old_reports(self):
        try:
            files = []
            for f in os.listdir(REPORTS_DIR):
                if f.startswith("backtest_") and f.endswith(".json"):
                    full = os.path.join(REPORTS_DIR, f)
                    files.append((os.path.getmtime(full), full))
            files.sort(reverse=True)
            for _, old_file in files[MAX_REPORTS:]:
                os.remove(old_file)
                logger.info(f"🗑️ Removed old report: {old_file}")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

    async def run(self, days: int = 30, max_tokens: int = 300, progress_callback=None) -> dict:
        logger.info(f"🚀 Starting {days}-day backtest...")
        start_ts = datetime.now(timezone.utc).timestamp()

        if self.send_msg:
            await self.send_msg(
                f"🧪 <b>Backtest শুরু!</b>\n"
                f"📅 {days} দিনের ডেটা কালেক্ট হচ্ছে...\n"
                f"⏱️ আনুমানিক ৩০-৯০ মিনিট লাগবে"
            )

        tokens = await self.collect_tokens(days, max_tokens)
        if not tokens:
            logger.error("No tokens collected")
            return {"error": "No tokens found"}

        results = []
        # Build token map for force_train
        token_map = {t["address"]: t for t in tokens}

        for i, token in enumerate(tokens):
            result = await self.evaluate_token(token)
            results.append(result)

            if (i + 1) % 25 == 0:
                logger.info(f"Evaluated {i+1}/{len(tokens)}")
                if progress_callback:
                    await progress_callback(i + 1, len(tokens))

        # BUG FIX: Force train from all backtest results
        # This bypasses the duplicate check that was blocking training
        trained_pumps, trained_early, golden_promoted = self._force_train_from_results(results, token_map)

        metrics = self.calculate_metrics(results)
        metrics["elapsed_seconds"] = round(datetime.now(timezone.utc).timestamp() - start_ts, 1)
        metrics["period_days"] = days
        metrics["trained_pumps"] = trained_pumps
        metrics["trained_early_pumps"] = trained_early
        metrics["golden_promoted"] = golden_promoted

        json_path = self._save_report_files(metrics, results, days)

        if self.send_msg:
            report = self._format_telegram_report(metrics, days)
            report += f"\n\n📚 <b>Training:</b>\n"
            report += f"🚀 Pumps trained: <b>{trained_pumps}</b>\n"
            report += f"🎯 Early pumps trained: <b>{trained_early}</b>\n"
            report += f"🌟 Golden patterns: <b>{golden_promoted}</b>"
            await self.send_msg(report)

        logger.info(f"✅ Backtest complete in {metrics['elapsed_seconds']}s")
        logger.info(f"📊 Win rate: {metrics['win_rate']}%, Precision: {metrics['precision']}%, Recall: {metrics['recall']}%")
        logger.info(f"📚 Trained: {trained_pumps} pumps, {trained_early} early, {golden_promoted} golden")

        return {
            "metrics": metrics,
            "json_path": json_path,
            "results": results,
        }


async def run_backtest_async(session, days: int = 30, max_tokens: int = 300, send_msg_func=None):
    dex = DexScreenerClient(session)
    helius = HeliusClient(session)
    engine = BacktestEngine(session, dex, helius, send_msg_func)
    return await engine.run(days, max_tokens)
