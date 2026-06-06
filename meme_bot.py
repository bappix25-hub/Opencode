import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

import aiohttp
from telegram import Bot
from telegram.ext import Application

from bot_state import BotState, TrackedCoin, CoinInfo
from config import config
from dex_client import DexScreenerClient
from rugcheck_client import RugcheckClient
from helius_client import HeliusClient
from pumpportal_ws import PumpPortalWS
from telegram_bot import TelegramHandlers, register_handlers
from learner import (
    score_coin, score_launch, record_signal, update_signal_result, update_signal_ath,
    get_stats, get_daily_report, learn_pump, learn_dump,
    extract_launch_pattern, learn_pump_with_launch, verify_pump, get_launch_age,
    get_adaptive_threshold, is_duplicate, auto_learn_from_tracking,
    purge_honeypot_patterns, save_honeypot_blocklist, load_honeypot_blocklist,
    learn_early_pump
)
from github_sync import sync_to_github, restore_from_github
from utils import format_number, gmgn_link, setup_logging
from backtest import BacktestEngine, REPORTS_DIR, MAX_REPORTS
from social_signals import SocialSignalEngine
from signal_filter import SignalFilter
from verify_loop import VerifyLoop
from honeypot_detector import HoneypotDetector
from paper_trader import get_paper_trader

logger = setup_logging("meme_bot")

async def send_msg(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(
            chat_id=config.chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Send error: {e}")

class MemeBot:
    def __init__(self):
        self.state = BotState()
        self.session: aiohttp.ClientSession = None
        self.dex: DexScreenerClient = None
        self.rugcheck: RugcheckClient = None
        self.helius: HeliusClient = None
        self.social: SocialSignalEngine = None
        self.filter_engine: SignalFilter = None
        self.verify_loop: VerifyLoop = None
        self.honeypot: HoneypotDetector = None
        self.pumpportal: PumpPortalWS = None
        self.telegram_app: Application = None
        self.handlers: TelegramHandlers = None
        self.paper_trader = get_paper_trader()
        self._shutdown_event = asyncio.Event()
        self._tasks: list = []
        self._last_internet_ok: bool = True

    async def start(self):
        self.session = aiohttp.ClientSession()
        self.dex = DexScreenerClient(self.session)
        self.rugcheck = RugcheckClient(self.session)
        self.helius = HeliusClient(self.session)
        self.social = SocialSignalEngine(self.session)
        self.filter_engine = SignalFilter()
        self.verify_loop = VerifyLoop(self.dex, self.filter_engine, lambda t: send_msg(self.telegram_app.bot, t))
        self.honeypot = HoneypotDetector(self.session, rugcheck=self.rugcheck, helius=self.helius, dex=self.dex)

        self.pumpportal = PumpPortalWS(
            on_new_token=self._on_new_token,
            on_migration=self._on_migration,
            on_trade=self._on_trade
        )

        self.telegram_app = Application.builder().token(config.bot_token).build()
        self.handlers = TelegramHandlers(self.state, self.dex, self.session, self.filter_engine, self.verify_loop, self.paper_trader)
        register_handlers(self.telegram_app, self.handlers)

        await restore_from_github()

        try:
            hp_set, dep_set = load_honeypot_blocklist()
            for a in hp_set:
                await self.state.mark_honeypot(a)
            for d in dep_set:
                await self.state.add_blocked_deployer(d)
            if hp_set or dep_set:
                logger.info(
                    f"♻️ হানিপট ব্লকলিস্ট লোড: {len(hp_set)} addr, {len(dep_set)} deployer"
                )
            purged = purge_honeypot_patterns(hp_set)
            if purged.get("moved", 0) > 0:
                logger.info(
                    f"🧹 Purge: {purged['moved']} honeypot pump patterns → dump"
                )
        except Exception as e:
            logger.debug(f"purge/load blocklist error: {e}")

        logger.info("🚀 বট চালু হচ্ছে...")

        self._tasks = [
            asyncio.create_task(self.pumpportal.connect(), name="pumpportal"),
            asyncio.create_task(self.realtime_scan_loop(), name="realtime"),
            asyncio.create_task(self.history_scan_loop(), name="history"),
            asyncio.create_task(self.cleanup_loop(), name="cleanup"),
            asyncio.create_task(self.check_signal_results_loop(), name="signal_check"),
            asyncio.create_task(self.track_outcomes_loop(), name="track_outcomes"),
            asyncio.create_task(self.connection_monitor_loop(), name="conn_monitor"),
        ]

        if config.enable_github_sync:
            self._tasks.append(asyncio.create_task(self.github_sync_loop(), name="github_sync"))

        self._tasks.append(asyncio.create_task(self.backtest_loop(), name="backtest"))
        self._tasks.append(asyncio.create_task(self.daily_summary_loop(), name="daily_summary"))

        if config.paper_trading:
            self._tasks.append(asyncio.create_task(self.paper_trading_loop(), name="paper_trading"))
            await send_msg(self.telegram_app.bot,
                f"📄 <b>Paper Trading চালু!</b>\n"
                f"💰 ব্যালেন্স: <b>{self.paper_trader.state.current_sol:.4f} SOL</b>\n"
                f"📦 প্রতি বাই: <b>{config.paper_trade_sol_per_buy:.4f} SOL</b>\n"
                f"🎯 Auto TP/SL + 3h timeout সক্রিয়"
            )

        await send_msg(self.telegram_app.bot, "🤖 <b>বট v3 চালু!</b>\n✅ 5x filter + Auto-verify + Social signals + Paper Trading সক্রিয়")

        try:
            await self.telegram_app.initialize()
            await self.telegram_app.start()
            await self.telegram_app.updater.start_polling()
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self):
        if self._shutdown_event.is_set():
            return
        logger.info("Shutting down gracefully...")
        self._shutdown_event.set()
        for task in self._tasks:
            task.cancel()
        if self.pumpportal:
            await self.pumpportal.close()
        if self.telegram_app:
            try:
                await self.telegram_app.updater.stop()
                await self.telegram_app.stop()
                await self.telegram_app.shutdown()
            except Exception as e:
                logger.error(f"Telegram shutdown error: {e}")
        if self.session:
            await self.session.close()
        logger.info("✅ Shutdown complete")

    async def _on_new_token(self, data: dict):
        asyncio.create_task(self.process_new_token(data))

    async def _on_migration(self, data: dict):
        asyncio.create_task(self.handle_migration(data))

    async def _is_known_honeypot(self, address: str) -> bool:
        if not address:
            return False
        if await self.state.is_honeypot(address):
            return True
        if await self.state.is_blacklisted(address):
            return True
        return False

    async def _on_trade(self, data: dict):
        address = data.get("mint")
        if not address:
            return
        launch_data = await self.state.get_launch_tracking(address)
        if not launch_data:
            return
        tx_type = data.get("txType", "")
        wallet = data.get("traderPublicKey", "")
        amount = float(data.get("solAmount", 0) or data.get("tokenAmount", 0) or 0)
        await self.state.update_launch_tx(address, tx_type, wallet, amount)
        await self.check_pre_migration_signal(address)

    async def process_new_token(self, data: dict):
        address = data.get("mint")
        if not address:
            return
        if await self.state.is_blacklisted(address):
            return
        if await self.state.is_honeypot(address):
            return
        if await self.state.get_tracked_coin(address):
            return
        symbol = data.get("symbol", "???")

        deployer = data.get("traderPublicKey", "") or data.get("deployer", "")
        if deployer and await self.state.is_deployer_blocked(deployer):
            logger.info(f"🚫 ডেপ্লয়ার ব্লক: {symbol} ({deployer[:8]}...)")
            return

        await asyncio.sleep(3)
        rug = await self.rugcheck.check_token(address, symbol)
        if rug and rug.is_risky:
            await self.state.mark_honeypot(address)
            if deployer:
                await self.state.add_blocked_deployer(deployer)
            logger.info(f"🍯 হানিপট: {symbol} | {rug.risks[:2]}")
            return

        hp_report = None
        try:
            hp_report = await self.honeypot.check(address, symbol)
        except Exception as e:
            logger.debug(f"honeypot check error: {e}")
        if hp_report and hp_report.is_honeypot:
            await self.state.mark_honeypot(address)
            if deployer:
                await self.state.add_blocked_deployer(deployer)
            logger.info(
                f"🍯 হানিপট (multi-layer): {symbol} | {hp_report.reasons[:2]}"
            )
            return

        holders = await self.helius.get_holder_count(address)
        from bot_state import LaunchData
        raw_price = data.get("initialBuy", 0) or 0
        if isinstance(raw_price, dict):
            raw_price = raw_price.get("amount", 0) or 0
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            price = 0.0
        now_ts = datetime.now(timezone.utc).timestamp()

        existing_tokens = await self.state.get_deployer_tokens(deployer) if deployer else []
        if len(existing_tokens) > 2:
            logger.info(f"⚠️ Bundle detected: {symbol} deployer {deployer[:8]}... has {len(existing_tokens)} tokens")

        launch_data = LaunchData(
            name=data.get("name", "Unknown"),
            symbol=symbol,
            first_seen=now_ts,
            launch_time=now_ts,
            volume=price,
            holders=holders or 0,
            lp_locked=rug.lp_locked if rug else 0,
            deployer_wallet=deployer,
        )
        await self.state.add_launch_tracking(address, launch_data)
        if holders is not None and holders < 3:
            logger.info(f"📊 শেখার জন্য ট্র্যাক (low holders): {symbol} ({holders}h)")
        else:
            logger.info(f"🆕 লঞ্চ ট্র্যাক: {symbol} (deployer: {deployer[:8] if deployer else 'unknown'}...)")

    async def check_pre_migration_signal(self, address: str):
        if await self.state.is_alerted(address):
            return
        launch_data = await self.state.get_launch_tracking(address)
        if not launch_data:
            return
        age = datetime.now(timezone.utc).timestamp() - launch_data.launch_time
        if age < 30:
            return
        logger.debug(
            f"pre-mig {launch_data.symbol}: age={int(age)}s "
            f"buys={launch_data.buy_count} sells={launch_data.sell_count} "
            f"holders={launch_data.holders}"
        )

        buy_sell_ratio = launch_data.buy_count / max(launch_data.sell_count, 1)
        unique_wallets = len(launch_data.unique_wallets)

        if unique_wallets < 2:
            logger.debug(f"🚫 {launch_data.symbol}: Red flag - {unique_wallets} unique wallets (min 2)")
            return
        if launch_data.holders < 5:
            logger.debug(f"🚫 {launch_data.symbol}: Red flag - {launch_data.holders} holders (min 5)")
            return
        if launch_data.buy_count < 3:
            return

        red_flags = []
        red_flag_penalty = 0.0

        if unique_wallets < 3:
            red_flags.append("⚠️ Very few unique wallets")
            red_flag_penalty += 0.3
        if launch_data.holders < 10:
            red_flags.append("⚠️ Low holder count")
            red_flag_penalty += 0.15
        if launch_data.holders < 3:
            red_flags.append("🚨 Suspiciously low holders")
            red_flag_penalty += 0.4

        launch_dict = {
            "buy_count": launch_data.buy_count,
            "sell_count": launch_data.sell_count,
            "unique_wallets": unique_wallets,
            "volume": launch_data.volume,
            "buy_sell_ratio": buy_sell_ratio
        }
        ai_score, reason = score_launch(launch_dict)
        threshold = get_adaptive_threshold()

        social_score = 0.0
        try:
            social_score, _ = await self.social.calculate_social_score(address, launch_data.symbol)
            if not isinstance(social_score, (int, float)):
                social_score = 0.0
        except Exception as e:
            logger.debug(f"Social score error: {e}")

        if social_score < 0.1:
            red_flags.append("⚠️ No social presence")
            red_flag_penalty += 0.2

        bonding_boost = 0.0
        bonding_reasons = []

        if age < 60:
            if launch_data.buy_count >= 8 and buy_sell_ratio >= 4.0 and unique_wallets >= 5:
                bonding_boost += 0.25
                bonding_reasons.append("🔥 Strong early frenzy")
            if unique_wallets >= 5 and launch_data.buy_count >= 10:
                bonding_boost += 0.20
                bonding_reasons.append("👥 Multi-wallet demand (5+ wallets)")
        elif age < 180:
            if launch_data.buy_count >= 15 and buy_sell_ratio >= 3.0 and unique_wallets >= 5:
                bonding_boost += 0.20
                bonding_reasons.append("📈 Strong buy pressure (verified)")
            if unique_wallets >= 8:
                bonding_boost += 0.15
                bonding_reasons.append("🌐 Diverse buyers (8+ wallets)")
        elif age < 300:
            if launch_data.buy_count >= 20 and buy_sell_ratio >= 2.5 and unique_wallets >= 8:
                bonding_boost += 0.15
                bonding_reasons.append("📊 Sustained demand")
            if unique_wallets >= 10:
                bonding_boost += 0.10
                bonding_reasons.append("👥 Growing community")

        if launch_data.holders >= 10 and launch_data.holders <= 100:
            bonding_boost += 0.10
            bonding_reasons.append("👥 Healthy holder range")

        if launch_data.buy_velocity > 0:
            try:
                from learner import load_data
                model = load_data().get("model", {})
                avg_vel = model.get("avg_early_pump_velocity", 0)
                if avg_vel > 0:
                    if launch_data.buy_velocity >= avg_vel * 1.5:
                        bonding_boost += 0.25
                        bonding_reasons.append("⚡ High velocity frenzy")
                    elif launch_data.buy_velocity >= avg_vel:
                        bonding_boost += 0.15
                        bonding_reasons.append("⚡ Above avg velocity")
            except Exception:
                pass

        if launch_data.curve_fill_pct > 0:
            if launch_data.curve_fill_pct >= 80:
                bonding_boost += 0.15
                bonding_reasons.append("📈 Curve nearly full")
            elif launch_data.curve_fill_pct >= 60:
                bonding_boost += 0.10
                bonding_reasons.append("📈 Curve filling fast")

        real_mcap = getattr(self, "_last_pre_mig_pair_mcap", 0) or 0
        real_liq = getattr(self, "_last_pre_mig_pair_liq", 0) or 0
        real_vol_liq = (real_mcap / max(real_liq, 1)) if (real_mcap and real_liq) else 0.3
        safe_volume = launch_data.volume if isinstance(launch_data.volume, (int, float)) else 0.0
        pattern = {
            "mcap": real_mcap if real_mcap > 0 else safe_volume * 1000,
            "liquidity": real_liq if real_liq > 0 else safe_volume * 50,
            "vol_liq_ratio": real_vol_liq,
            "buy_sell_ratio": buy_sell_ratio,
            "buy_count": launch_data.buy_count,
            "sell_count": launch_data.sell_count,
        }

        should_signal, final_score, filter_reason = self.filter_engine.should_signal(
            address, pattern, ai_score=ai_score,
            social_score=social_score, age_seconds=age
        )

        final_score += bonding_boost
        final_score -= red_flag_penalty
        final_score = max(0.0, final_score)

        effective_threshold = max(threshold, self.filter_engine.min_threshold)
        effective_threshold += red_flag_penalty * 0.5

        logger.debug(
            f"pre-mig {launch_data.symbol}: age={int(age)}s "
            f"buys={launch_data.buy_count} sells={launch_data.sell_count} "
            f"ai={ai_score:.2f} soc={social_score:.2f} bond={bonding_boost:.2f} "
            f"red={red_flag_penalty:.2f} final={final_score:.2f} thr={effective_threshold:.2f} "
            f"signal={should_signal} ({filter_reason})"
        )

        if final_score >= effective_threshold and bonding_boost > 0:
            if red_flags:
                logger.info(f"🚫 {launch_data.symbol}: Blocked by red flags: {red_flags}")
                return

            if logger.isEnabledFor(logging.INFO):
                logger.info(
                    f"⚡ pre-mig check {launch_data.symbol}: age={int(age)}s "
                    f"buys={launch_data.buy_count} sells={launch_data.sell_count} "
                    f"ai={ai_score:.2f} soc={social_score:.2f} bond={bonding_boost:.2f} "
                    f"final={final_score:.2f} thr={effective_threshold:.2f} → SIGNAL!"
                )
            symbol = launch_data.symbol
            name = launch_data.name
            confidence_pct = int(final_score * 100)
            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
            link = gmgn_link(address)
            social_pct = int(social_score * 100)
            bonding_text = "\n".join([f"  • {r}" for r in bonding_reasons]) if bonding_reasons else "  • Standard scoring"
            velocity_text = f"⚡ {launch_data.buy_velocity:.1f} buys/min" if launch_data.buy_velocity > 0 else ""
            curve_text = f"📈 Curve: ~{launch_data.curve_fill_pct:.0f}%" if launch_data.curve_fill_pct > 0 else ""
            avg_time_between = ""
            if len(launch_data.buy_timestamps) >= 2:
                diffs = [launch_data.buy_timestamps[i+1] - launch_data.buy_timestamps[i] for i in range(len(launch_data.buy_timestamps)-1)]
                avg_diff = sum(diffs) / len(diffs)
                avg_time_between = f"⏱️ Avg buy gap: {avg_diff:.0f}s"

            await send_msg(self.telegram_app.bot,
                f"⚡ <b>প্রি-মাইগ্রেশন সিগন্যাল!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🏷️ <b>{name}</b> (${symbol})\n"
                f"🎯 কনফিডেন্স: {confidence_bar} <b>{confidence_pct}%</b>\n"
                f"🧠 <i>{reason}</i>\n"
                f"📊 Buy: <b>{launch_data.buy_count}</b> | Sell: <b>{launch_data.sell_count}</b>\n"
                f"👥 Unique wallets: <b>{unique_wallets}</b>\n"
                f"👤 Holders: <b>{launch_data.holders}</b>\n"
                f"🌐 Social: <b>{social_pct}%</b>\n"
                f"⏱️ বয়স: <b>{int(age//60)}m {int(age%60)}s</b> (launch time থেকে)\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔗 <b>Bonding Curve Analysis:</b>\n"
                f"{bonding_text}\n"
                f"{velocity_text}\n{curve_text}\n{avg_time_between}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>মাইগ্রেশনের আগে! DYOR করুন!</i>\n"
                f"🔗 <a href='{link}'>GMGN</a>"
            )
            await self.state.add_alerted(address)
            launch_data.pre_signal_sent = True
            logger.info(f"⚡ প্রি-মাইগ্রেশন সিগন্যাল: {symbol} স্কোর: {final_score:.2f} (ai={ai_score:.2f}, social={social_score:.2f}, bond={bonding_boost:.2f}, velocity={launch_data.buy_velocity:.1f}/min)")

    async def _on_migration(self, data: dict):
        await self.handle_migration(data)

    async def handle_migration(self, data: dict):
        address = data.get("mint")
        symbol = data.get("symbol", "???")
        if not address:
            return
        launch_data = await self.state.get_launch_tracking(address)
        if not launch_data:
            return
        logger.info(f"🚀 Migration: {symbol}")
        launch_data.migration_time = datetime.now(timezone.utc).timestamp()
        await asyncio.sleep(10)
        pair = await self.dex.fetch_pair_data(address)
        if not pair:
            return
        price = float(pair.get("priceUsd", 0) or 0)
        if price > 0 and not await self.state.get_tracked_coin(address):
            launch_data.migration_price = price
            tracked = TrackedCoin(
                initial_price=price,
                name=launch_data.name,
                symbol=launch_data.symbol,
                first_seen=launch_data.launch_time,
                launch_time=launch_data.launch_time,
                holders=launch_data.holders,
                lp_locked=launch_data.lp_locked,
                deployer_wallet=launch_data.deployer_wallet,
                initial_holders=launch_data.holders,
            )
            await self.state.add_tracked_coin(address, tracked)
            logger.info(f"✅ মাইগ্রেশন ট্র্যাক: {symbol} | price: ${price:.8f} | buys: {launch_data.buy_count} | velocity: {launch_data.buy_velocity:.1f}/min")

    async def realtime_scan_loop(self):
        sync_counter = 0
        while True:
            try:
                stats = await self.state.get_stats()
                if not stats["bot_active"]:
                    await asyncio.sleep(30)
                    continue

                new_tokens = await self.dex.fetch_new_solana_pairs()
                for t in new_tokens[:15]:
                    addr = t.get("tokenAddress") or t.get("address")
                    if not addr:
                        continue
                    if await self.state.get_tracked_coin(addr):
                        continue
                    if await self.state.is_blacklisted(addr):
                        continue
                    await asyncio.sleep(1)
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        continue
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    mcap = float(pair.get("fdv", 0) or 0)
                    if liquidity < config.min_liquidity or mcap < config.min_mcap or mcap > config.max_mcap:
                        continue
                    price = float(pair.get("priceUsd", 0) or 0)
                    if price > 0:
                        pair_age = get_launch_age(pair) or 0
                        launch_ts = datetime.now(timezone.utc).timestamp() - pair_age
                        tracked = TrackedCoin(
                            initial_price=price,
                            name=pair.get("baseToken", {}).get("name", "Unknown"),
                            symbol=pair.get("baseToken", {}).get("symbol", "???"),
                            first_seen=launch_ts,
                            launch_time=launch_ts,
                        )
                        await self.state.add_tracked_coin(addr, tracked)

                launch_dict = dict(self.state.launch_tracking)
                if launch_dict:
                    logger.info(f"🔍 pre-mig scan: {len(launch_dict)} launches in queue")
                for addr, _ld in list(launch_dict.items())[:30]:
                    if await self.state.is_blacklisted(addr):
                        continue
                    pair = await self.dex.fetch_pair_data(addr)
                    if pair:
                        ld = self.state.launch_tracking.get(addr)
                        if ld:
                            if not ld.initial_price:
                                pair_price = float(pair.get("priceUsd", 0) or 0)
                                if pair_price > 0:
                                    ld.initial_price = pair_price
                            txns = pair.get("txns") or {}
                            h1 = txns.get("h1") or {}
                            h24 = txns.get("h24") or {}
                            buys_1h = int(h1.get("buys", 0) or 0)
                            sells_1h = int(h1.get("sells", 0) or 0)
                            vol_1h = float((pair.get("volume") or {}).get("h1", 0) or 0)
                            vol_24h = float((pair.get("volume") or {}).get("h24", 0) or 0)
                            real_mcap = float(pair.get("fdv", 0) or 0)
                            real_liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
                            self._last_pre_mig_pair_mcap = real_mcap
                            self._last_pre_mig_pair_liq = real_liq
                            unique_from_txns = buys_1h + sells_1h
                            if len(ld.unique_wallets) > ld.holders:
                                ld.holders = len(ld.unique_wallets)
                            elif unique_from_txns > ld.holders:
                                ld.holders = unique_from_txns
                            if buys_1h > ld.buy_count or sells_1h > ld.sell_count or vol_1h > ld.volume:
                                if buys_1h > ld.buy_count:
                                    ld.buy_count = buys_1h
                                if sells_1h > ld.sell_count:
                                    ld.sell_count = sells_1h
                                if vol_1h > ld.volume:
                                    ld.volume = vol_1h
                    await self.check_pre_migration_signal(addr)
                    await asyncio.sleep(0.3)

                for addr, coin_info in list((await self._get_tracked_dict()).items()):
                    if await self.state.is_blacklisted(addr):
                        continue
                    launch_data = await self.state.get_launch_tracking(addr)
                    if launch_data:
                        await self.check_pre_migration_signal(addr)
                    await asyncio.sleep(1)
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        continue
                    now_ts = datetime.now(timezone.utc).timestamp()
                    age = now_ts - coin_info.launch_time if coin_info.launch_time > 0 else 0
                    if age <= 0:
                        pair_age = get_launch_age(pair) or 0
                        age = pair_age
                    if age <= 0:
                        continue
                    current_price = float(pair.get("priceUsd", 0) or 0)
                    if coin_info.initial_price <= 0 or current_price <= 0:
                        continue
                    multiplier = current_price / coin_info.initial_price
                    mcap = float(pair.get("fdv", 0) or 0)
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    name = coin_info.name
                    symbol = coin_info.symbol
                    link = gmgn_link(addr)

                    if config.paper_trading:
                        try:
                            closed = await self.paper_trader.check_tp_sl(addr, current_price)
                            if closed:
                                emoji = "✅" if closed.pnl_sol >= 0 else "❌"
                                reason_map = {"tp_hit": "🎯 TP Hit", "sl_hit": "🛑 SL Hit"}
                                reason = reason_map.get(closed.exit_reason, closed.exit_reason)
                                await send_msg(self.telegram_app.bot,
                                    f"{emoji} <b>Paper {reason}!</b>\n"
                                    f"🏷️ ${symbol}\n"
                                    f"📈 ${closed.entry_price:.8f} → ${closed.exit_price:.8f}\n"
                                    f"💰 PnL: <b>{closed.pnl_sol:+.4f} SOL ({closed.pnl_pct:+.1f}%)</b>\n"
                                    f"💵 ব্যালেন্স: {self.paper_trader.state.current_sol:.4f} SOL"
                                )
                        except Exception as e:
                            logger.debug(f"Paper TP/SL check error: {e}")

                    if liquidity < 300:
                        await self.state.add_blacklisted(addr)
                        logger.info(f"🚫 লিকুইডিটি pull: {symbol}")
                        continue

                    if age > 86400:
                        verified, actual_multi = verify_pump(pair, config.pump_multiplier)
                        if verified:
                            await self.state.add_pump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            txs = await self.helius.get_launch_transactions(addr)
                            launch_pat = extract_launch_pattern(txs) if txs else None
                            learn_pump_with_launch(
                                {"name": name, "symbol": symbol}, pair, actual_multi,
                                launch_pat, addr, manual=False
                            )
                            holders = coin_info.holders
                            lp = coin_info.lp_locked
                            await send_msg(self.telegram_app.bot,
                                f"🚀 <b>পাম্প কয়েন!</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🏷️ <b>{name}</b> (${symbol})\n"
                                f"📈 পাম্প: <b>{actual_multi}x</b>\n"
                                f"💰 MCap: <b>{format_number(mcap)}</b>\n"
                                f"💧 লিকুইডিটি: <b>{format_number(liquidity)}</b>\n"
                                f"👥 হোল্ডার: <b>{holders}</b>\n"
                                f"🔒 LP লক: <b>{lp}%</b>\n"
                                f"🧠 <i>লঞ্চ প্যাটার্ন শেখা হয়েছে!</i>\n"
                                f"🔗 <a href='{link}'>GMGN</a>"
                            )
                            if config.enable_github_sync:
                                await sync_to_github(f"পাম্প: {symbol} {actual_multi}x")
                        else:
                            await self.state.add_dump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            learn_dump({"name": name, "symbol": symbol}, pair, addr, manual=False)
                        continue

                    if not await self.state.is_alerted(addr) and 0 < age <= 600:
                        launch_data = await self.state.get_launch_tracking(addr)
                        launch_dict = None
                        if launch_data:
                            launch_dict = {
                                "buy_count": launch_data.buy_count,
                                "unique_wallets": len(launch_data.unique_wallets),
                                "volume": launch_data.volume,
                            }
                        ai_score, reason = score_coin(
                            pair, {"name": name, "symbol": symbol}, age,
                            launch_data=launch_dict, is_post_migration=True
                        )
                        threshold = get_adaptive_threshold()

                        pattern = {
                            "mcap": mcap,
                            "liquidity": liquidity,
                            "vol_liq_ratio": pair.get("volume", {}).get("h24", 0) / max(liquidity, 1) if liquidity > 0 else 0,
                            "buy_sell_ratio": launch_data.buy_count / max(launch_data.sell_count, 1) if launch_data else 1,
                        }

                        social_score = 0.0
                        try:
                            social_score, _ = await self.social.calculate_social_score(addr, symbol)
                            if not isinstance(social_score, (int, float)):
                                social_score = 0.0
                        except Exception as e:
                            logger.debug(f"Social score error: {e}")

                        should_signal, final_score, filter_reason = self.filter_engine.should_signal(
                            addr, pattern, ai_score=ai_score,
                            social_score=social_score, age_seconds=age
                        )

                        effective_threshold = max(threshold, self.filter_engine.min_threshold)
                        if should_signal and ai_score >= effective_threshold:
                            holders = coin_info.holders
                            lp = coin_info.lp_locked
                            confidence_pct = int(final_score * 100)
                            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
                            social_pct = int(social_score * 100)
                            await send_msg(self.telegram_app.bot,
                                f"⚡ <b>আর্লি সিগন্যাল!</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🏷️ <b>{name}</b> (${symbol})\n"
                                f"🎯 কনফিডেন্স: {confidence_bar} <b>{confidence_pct}%</b>\n"
                                f"🧠 <i>{reason}</i>\n"
                                f"💵 দাম: <b>{current_price:.8f}</b>\n"
                                f"💰 MCap: <b>{format_number(mcap)}</b>\n"
                                f"💧 লিকুইডিটি: <b>{format_number(liquidity)}</b>\n"
                                f"👥 হোল্ডার: <b>{holders}</b>\n"
                                f"🔒 LP লক: <b>{lp}%</b>\n"
                                f"🌐 Social: <b>{social_pct}%</b>\n"
                f"⏱️ বয়স: <b>{int(age//60)}m {int(age%60)}s</b> (launch time থেকে)\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🔗 <a href='{link}'>GMGN</a>"
                            )
                            record_signal(addr, symbol, final_score, current_price, mcap)
                            from bot_state import SignalInfo
                            await self.state.add_signal(addr, SignalInfo(
                                symbol=symbol,
                                price_at_signal=current_price,
                                signal_time=now_ts
                            ))
                            await self.state.add_alerted(addr)

                            if config.paper_trading:
                                try:
                                    launch_vel = getattr(launch_data, 'buy_velocity', 0)
                                    launch_curve = getattr(launch_data, 'curve_fill_pct', 0)
                                    pos = await self.paper_trader.buy(
                                        addr, symbol, name, current_price,
                                        ai_score, social_score, final_score, age,
                                        launch_vel, launch_curve
                                    )
                                    if pos:
                                        await send_msg(self.telegram_app.bot,
                                            f"🟢 <b>Paper Buy!</b>\n"
                                            f"🏷️ ${symbol} @ ${current_price:.8f}\n"
                                            f"💰 {pos.sol_amount:.4f} SOL\n"
                                            f"🎯 TP: ${pos.tp_price:.8f} ({((pos.tp_price / pos.entry_price) - 1) * 100:+.0f}%)\n"
                                            f"🛑 SL: ${pos.sl_price:.8f} ({((pos.sl_price / pos.entry_price) - 1) * 100:+.0f}%)\n"
                                            f"💵 ব্যালেন্স: {self.paper_trader.state.current_sol:.4f} SOL"
                                        )
                                except Exception as e:
                                    logger.debug(f"Paper buy error: {e}")

                            try:
                                self.verify_loop.schedule_verification(
                                    addr, symbol, coin_info.launch_time or now_ts,
                                    current_price, social_score, final_score
                                )
                            except Exception as e:
                                logger.debug(f"Verify schedule error: {e}")

                sync_counter += 1
                if sync_counter >= config.github_sync_interval // max(config.scan_interval, 1):
                    if config.enable_github_sync:
                        await sync_to_github()
                    sync_counter = 0

                final_stats = await self.state.get_stats()
                logger.info(f"ট্র্যাক: {final_stats['tracked_coins']} | লঞ্চ: {final_stats['launch_tracking']} | পাম্প: {final_stats['pump_coins']}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"রিয়েলটাইম এরর: {e}")
            await asyncio.sleep(config.scan_interval)

    async def _get_tracked_dict(self) -> dict:
        async with self.state._lock:
            return dict(self.state.tracked_coins)

    async def history_scan_loop(self):
        while True:
            try:
                stats = await self.state.get_stats()
                if not stats["bot_active"]:
                    await asyncio.sleep(60)
                    continue
                logger.info("📚 হিস্ট্রি স্ক্যান...")
                boosted = await self.dex.fetch_boosted_pairs()
                new_tokens = await self.dex.fetch_new_solana_pairs()
                all_addrs = {}
                for t in boosted + new_tokens:
                    addr = t.get("tokenAddress") or t.get("address")
                    if addr:
                        all_addrs[addr] = t
                learned_pump = 0
                learned_dump = 0
                for addr in list(all_addrs.keys())[:40]:
                    if is_duplicate(addr):
                        continue
                    await asyncio.sleep(2)
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        continue
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    age = get_launch_age(pair)
                    if liquidity < 1000 or age is None or age > 86400:
                        continue
                    verified, actual_multi = verify_pump(pair, config.pump_multiplier)
                    coin_info = {
                        "name": pair.get("baseToken", {}).get("name", "Unknown"),
                        "symbol": pair.get("baseToken", {}).get("symbol", "???"),
                    }
                    if verified:
                        txs = await self.helius.get_launch_transactions(addr)
                        launch_pat = extract_launch_pattern(txs) if txs else None
                        ok, msg = learn_pump_with_launch(coin_info, pair, actual_multi, launch_pat, addr, manual=False)
                        if ok:
                            learned_pump += 1
                            link = gmgn_link(addr)
                            launch_info = " | লঞ্চ ডেটা: ✅" if launch_pat else ""
                            await send_msg(self.telegram_app.bot,
                                f"📚 <b>পাম্প শেখা!</b>\n"
                                f"🏷️ <b>{coin_info['name']}</b> (${coin_info['symbol']})\n"
                                f"📈 <b>{actual_multi}x</b> | ⏱️ {int((age or 0)/60)}m{launch_info}\n"
                                f"💰 {format_number(pair.get('fdv', 0))}\n"
                                f"🔗 <a href='{link}'>GMGN</a>"
                            )
                    elif age and age > 3600:
                        h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
                        if h24 < 100:
                            ok, msg = learn_dump(coin_info, pair, addr, manual=False)
                            if ok:
                                learned_dump += 1
                if learned_pump > 0 or learned_dump > 0:
                    if config.enable_github_sync:
                        await sync_to_github(f"হিস্ট্রি: পাম্প {learned_pump} ডাম্প {learned_dump}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"হিস্ট্রি এরর: {e}")
            await asyncio.sleep(config.history_scan_interval)

    async def check_signal_results_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                unchecked = await self.state.get_unchecked_signals()
                now = datetime.now(timezone.utc).timestamp()
                for addr, sig_info in list(unchecked.items()):
                    age = now - sig_info.signal_time
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        continue
                    current_price = float(pair.get("priceUsd", 0) or 0)
                    if current_price <= 0:
                        continue

                    ath_price, ath_mult = update_signal_ath(addr, current_price)

                    if age < 10800:
                        continue

                    update_signal_result(addr, current_price)
                    multiplier = current_price / sig_info.price_at_signal if sig_info.price_at_signal > 0 else 0
                    emoji = "✅" if multiplier >= 2.0 else "❌"

                    ath_emoji = "📈" if ath_mult >= 3 else "📊" if ath_mult >= 2 else "📉"
                    ath_text = f"{ath_emoji} ATH: <b>{ath_mult:.2f}x</b> (${ath_price:.8f})" if ath_mult > 1.0 else ""

                    pnl_text = ""
                    if config.paper_trading and self.paper_trader:
                        closed = await self.paper_trader.force_close(addr, current_price)
                        if closed:
                            pnl_text = f"\n💰 Paper PnL: <b>{closed.pnl_sol:+.4f} SOL ({closed.pnl_pct:+.1f}%)</b>"

                    await send_msg(self.telegram_app.bot,
                        f"{emoji} <b>সিগন্যাল ফলাফল (3h)!</b>\n"
                        f"🏷️ ${sig_info.symbol}\n"
                        f"📊 বর্তমান: <b>{multiplier:.2f}x</b>\n"
                        f"{ath_text}{pnl_text}"
                    )
                    await self.state.mark_signal_checked(addr)
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"সিগন্যাল চেক এরর: {e}")

    async def cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(config.cleanup_interval)
                await self.state.cleanup_old_entries()
                try:
                    async with self.state._lock:
                        hp_snapshot = set(self.state.honeypot_addresses)
                        dep_snapshot = set(self.state.blocked_deployers)
                    save_honeypot_blocklist(hp_snapshot, dep_snapshot)
                except Exception as e:
                    logger.debug(f"save blocklist error: {e}")
                logger.info("🧹 ক্লিনআপ সম্পন্ন")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ক্লিনআপ এরর: {e}")

    async def track_outcomes_loop(self):
        """
        Stage 2 learning: for every launch we tracked (even low-holder ones),
        re-check price at T+5/15/60 minutes and feed learn_pump/learn_dump.

        Also re-runs honeypot detection at each window — late-revealing honeypots
        are still detected and recorded as dumps (not pumps).
        """
        from bot_state import LaunchData
        eval_offsets = [300, 900, 3600]
        await asyncio.sleep(120)
        while True:
            try:
                now = datetime.now(timezone.utc).timestamp()
                for addr, ld in list(self.state.launch_tracking.items()):
                    age = now - ld.launch_time
                    next_offset = None
                    for off in eval_offsets:
                        if age >= off and not ld.eval_done.get(str(off), False):
                            next_offset = off
                            break
                    if next_offset is None:
                        continue

                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        ld.eval_done[str(next_offset)] = True
                        continue
                    current_price = float(pair.get("priceUsd", 0) or 0)
                    initial_price = ld.initial_price
                    if initial_price <= 0:
                        initial_price = current_price or 0.000001

                    hp_report = None
                    try:
                        hp_report = await self.honeypot.check(addr, ld.symbol, pair=pair)
                    except Exception as e:
                        logger.debug(f"honeypot recheck error: {e}")
                    is_hp = bool(hp_report and hp_report.is_honeypot)

                    if is_hp and not await self.state.is_honeypot(addr):
                        await self.state.mark_honeypot(addr)
                        if ld.deployer_wallet:
                            await self.state.add_blocked_deployer(ld.deployer_wallet)
                        logger.info(
                            f"🍯 লেট-রিভিল হানিপট: {ld.symbol} @ T+{int(next_offset)}s | {hp_report.reasons[:2]}"
                        )

                    launch_dict = {
                        "buy_count": ld.buy_count,
                        "sell_count": ld.sell_count,
                        "unique_wallets": len(ld.unique_wallets),
                        "volume": ld.volume,
                    }

                    if next_offset == 300 and not is_hp:
                        multiplier = current_price / initial_price if initial_price else 0
                        if multiplier >= 2.0:
                            try:
                                ok, msg = learn_early_pump(
                                    addr, ld.symbol, ld.name, pair,
                                    launch_dict, age, multiplier
                                )
                                if ok:
                                    logger.info(f"🎯 Early pump trained: ${ld.symbol} {multiplier:.2f}x @ T+5min")
                                    if self.telegram_app and self.telegram_app.bot:
                                        link = gmgn_link(addr)
                                        await send_msg(self.telegram_app.bot,
                                            f"🎯 <b>Early Pump শেখা!</b>\n"
                                            f"🏷️ ${ld.symbol}\n"
                                            f"📈 {multiplier:.2f}x @ T+5min\n"
                                            f"📊 Buy: {ld.buy_count} | Wallets: {len(ld.unique_wallets)}\n"
                                            f"🔗 <a href='{link}'>GMGN</a>"
                                        )
                            except Exception as e:
                                logger.debug(f"Early pump learn error: {e}")

                    learned, kind, msg = auto_learn_from_tracking(
                        address=addr,
                        symbol=ld.symbol,
                        name=ld.name,
                        launch_dict=launch_dict,
                        current_price=current_price,
                        initial_price=initial_price,
                        holders=ld.holders,
                        lp_locked=ld.lp_locked,
                        deployer_wallet=ld.deployer_wallet,
                        tx_history=ld.tx_history,
                        age_seconds=age,
                        pump_threshold=config.pump_multiplier,
                        is_honeypot=is_hp,
                        honeypot_reasons=hp_report.reasons if hp_report else None,
                    )

                    if learned:
                        multiplier = current_price / initial_price if initial_price else 0
                        suffix = f" (honeypot)" if is_hp else ""
                        logger.info(
                            f"📚 অটো-শেখা: ${ld.symbol} → {kind} {multiplier:.2f}x @ T+{int(next_offset)}s{suffix}"
                        )
                    ld.eval_done[str(next_offset)] = True

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"track_outcomes_loop error: {e}")
                await asyncio.sleep(60)

    async def connection_monitor_loop(self):
        """
        Periodically check internet connectivity. If it drops for >3 minutes,
        log offline state. When it comes back, force-reconnect the PumpPortal WS
        and all scan loops.
        """
        check_every = 30
        offline_threshold = 3
        consecutive_failures = 0
        was_offline = False
        await asyncio.sleep(60)
        while True:
            try:
                ok = False
                try:
                    async with self.session.get(
                        "https://api.telegram.org",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        ok = r.status < 500
                except Exception:
                    ok = False

                if ok:
                    if was_offline or not self._last_internet_ok:
                        logger.info("🌐 ইন্টারনেট ফিরে এসেছে — টাস্ক পুনরায় চালু")
                        try:
                            if self.pumpportal and not getattr(self.pumpportal, "_running", False):
                                asyncio.create_task(self.pumpportal.connect())
                        except Exception as e:
                            logger.debug(f"PumpPortal restart error: {e}")
                    was_offline = False
                    consecutive_failures = 0
                    self._last_internet_ok = True
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= offline_threshold and not was_offline:
                        logger.warning(
                            f"⚠️ ইন্টারনেট ড্রপ ({consecutive_failures}× ব্যর্থ) — রিস্টার্টের জন্য অপেক্ষা"
                        )
                        was_offline = True
                    self._last_internet_ok = False

                await asyncio.sleep(check_every)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"connection_monitor_loop error: {e}")
                await asyncio.sleep(check_every)

    async def paper_trading_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                closed = await self.paper_trader.timeout_close_all(self.dex)
                for pos in closed:
                    emoji = "✅" if pos.pnl_sol >= 0 else "❌"
                    await send_msg(self.telegram_app.bot,
                        f"{emoji} <b>Paper 3h Timeout!</b>\n"
                        f"🏷️ ${pos.symbol}\n"
                        f"📈 ${pos.entry_price:.8f} → ${pos.exit_price:.8f}\n"
                        f"💰 PnL: <b>{pos.pnl_sol:+.4f} SOL ({pos.pnl_pct:+.1f}%)</b>\n"
                        f"💵 ব্যালেন্স: {self.paper_trader.state.current_sol:.4f} SOL"
                    )
                open_count = len(self.paper_trader.get_open_positions())
                if open_count > 0 and open_count % 5 == 0:
                    logger.info(f"📄 Paper: {open_count} open positions, balance: {self.paper_trader.state.current_sol:.4f} SOL")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"paper_trading_loop error: {e}")

    async def github_sync_loop(self):
        while True:
            try:
                await asyncio.sleep(config.github_sync_interval)
                await sync_to_github()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GitHub sync loop error: {e}")

    async def backtest_loop(self):
        backtest_interval = 7 * 24 * 3600
        await asyncio.sleep(300)
        while True:
            try:
                stats = await self.state.get_stats()
                if not stats["bot_active"]:
                    await asyncio.sleep(3600)
                    continue
                logger.info("🧪 Scheduled 30-day backtest শুরু...")
                async with aiohttp.ClientSession() as bt_session:
                    dex = DexScreenerClient(bt_session)
                    helius = HeliusClient(bt_session)
                    async def bt_send(text):
                        await send_msg(self.telegram_app.bot, text)
                    engine = BacktestEngine(bt_session, dex, helius, bt_send)
                    result = await engine.run(days=30, max_tokens=300)
                    if "metrics" in result:
                        if config.enable_github_sync:
                            await sync_to_github(f"backtest: {result['metrics']['win_rate']}% win rate")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Backtest loop error: {e}", exc_info=True)
            await asyncio.sleep(backtest_interval)

    async def daily_summary_loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == 0 and now.minute < 2:
                    stats = await self.state.get_stats()
                    learner_stats = get_stats()
                    filter_stats = self.filter_engine.get_stats() if self.filter_engine else {}
                    verify_stats = self.verify_loop.get_stats() if self.verify_loop else {}

                    await send_msg(self.telegram_app.bot,
                        f"📋 <b>দৈনিক সামারি</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📅 {now.strftime('%Y-%m-%d')}\n\n"
                        f"<b>সিগন্যাল:</b>\n"
                        f"⚡ মোট verified: <b>{verify_stats.get('total_verified', 0)}</b>\n"
                        f"✅ Win rate: <b>{verify_stats.get('win_rate', 0)}%</b>\n"
                        f"🌟 5x rate: <b>{verify_stats.get('strong_rate', 0)}%</b>\n\n"
                        f"<b>AI Model:</b>\n"
                        f"🧠 Pump patterns: <b>{learner_stats['pump_patterns']}</b>\n"
                        f"📉 Dump patterns: <b>{learner_stats['dump_patterns']}</b>\n"
                        f"🎯 Accuracy: <b>{learner_stats['accuracy']}%</b>\n\n"
                        f"<b>Filter:</b>\n"
                        f"🌟 Golden: <b>{filter_stats.get('golden_count', 0)}</b>\n"
                        f"🚫 Blacklist: <b>{filter_stats.get('blacklist_count', 0)}</b>\n\n"
                        f"🟢 বট চালু আছে — 24/7 monitoring"
                    )

                    if config.enable_github_sync:
                        await sync_to_github(f"daily summary: {verify_stats.get('win_rate', 0)}% win")
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily summary error: {e}")
                await asyncio.sleep(60)

async def daily_report_loop(bot: Bot):
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour == 18 and now.minute < 2:
                report = get_daily_report()
                best = report.get("best_signal")
                best_text = f"${best['symbol']} → {best.get('result_multiplier', 0)}x" if best else "N/A"
                await send_msg(bot,
                    f"📋 <b>দৈনিক রিপোর্ট</b>\n"
                    f"📅 {report['date']}\n"
                    f"⚡ সিগন্যাল: <b>{report['signals_sent']}</b>\n"
                    f"🚀 পাম্প শেখা: <b>{report['pumps_learned']}</b>\n"
                    f"✅ সফল: <b>{report['successful']}/{report['checked']}</b>\n"
                    f"🏆 সেরা: <b>{best_text}</b>"
                )
                if config.enable_github_sync:
                    await sync_to_github("দৈনিক রিপোর্ট")
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"দৈনিক রিপোর্ট এরর: {e}")
            await asyncio.sleep(60)


def main():
    if sys.platform != "win32":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown_handler()))
            except NotImplementedError:
                pass

    bot = MemeBot()

    async def shutdown_handler():
        await bot.shutdown()

    try:
        bot.telegram_app = None
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        loop.close()


async def _async_main():
    bot = MemeBot()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
