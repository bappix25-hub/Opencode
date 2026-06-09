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
from birdeye_client import BirdeyeClient
from jupiter_client import JupiterClient
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
        self.birdeye: BirdeyeClient = None
        self.jupiter: JupiterClient = None
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
        self.birdeye = BirdeyeClient(self.session, config.birdeye_api_key)
        self.jupiter = JupiterClient(self.session)
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
                logger.info(f"♻️ হানিপট ব্লকলিস্ট লোড: {len(hp_set)} addr, {len(dep_set)} deployer")
            purged = purge_honeypot_patterns(hp_set)
            if purged.get("moved", 0) > 0:
                logger.info(f"🧹 Purge: {purged['moved']} honeypot pump patterns → dump")
        except Exception as e:
            logger.debug(f"purge/load blocklist error: {e}")

        logger.info("🚀 বট চালু হচ্ছে...")

        self._tasks = [
            asyncio.create_task(self.pumpportal.connect(), name="pumpportal"),
            asyncio.create_task(self.realtime_scan_loop(), name="realtime"),
            asyncio.create_task(self.history_scan_loop(), name="history"),
            asyncio.create_task(self.cleanup_loop(), name="cleanup"),
            asyncio.create_task(self.curve_refresh_loop(), name="curve_refresh"),
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

        self._tasks.append(asyncio.create_task(self.health_check_loop(), name="health_check"))
        self._tasks.append(asyncio.create_task(self.feature_request_loop(), name="feature_request"))

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

        # FIX: properly await task cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

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
            if not hasattr(self, '_trade_no_track_logged'):
                self._trade_no_track_logged = set()
            if address not in self._trade_no_track_logged:
                self._trade_no_track_logged.add(address)
                logger.info(f"TRADE NO TRACK: {address[:8]}... txType={data.get('txType')}")
            return
        tx_type = data.get("txType", "")
        wallet = data.get("traderPublicKey", "")
        if not wallet:
            if not hasattr(self, '_trade_debug_logged'):
                self._trade_debug_logged = set()
            if address not in self._trade_debug_logged:
                self._trade_debug_logged.add(address)
                logger.info(f"TRADE DEBUG keys={list(data.keys())} traderPK={data.get('traderPublicKey', 'MISSING')}")
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
            logger.info(f"🍯 হানিপট (multi-layer): {symbol} | {hp_report.reasons[:2]}")
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

        curve_progress = 0.0
        is_migrated = False
        try:
            bc_state = await self.helius.get_bonding_curve_state(address)
            if bc_state:
                curve_progress = float(bc_state.get("progress_pct", 0) or 0)
                is_migrated = bool(bc_state.get("complete", False))
        except Exception as e:
            logger.debug(f"bonding curve fetch error: {e}")

        launch_data = LaunchData(
            name=data.get("name", "Unknown"),
            symbol=symbol,
            first_seen=now_ts,
            launch_time=now_ts,
            volume=price,
            holders=holders or 0,
            curve_fill_pct=curve_progress,
            migration_time=now_ts if is_migrated else 0.0,
            lp_locked=rug.lp_locked if rug else 0,
            deployer_wallet=deployer,
        )
        await self.state.add_launch_tracking(address, launch_data)

        if holders is not None and holders < 3:
            logger.info(f"📊 ট্র্যাক (low holders): {symbol} ({holders}h)")
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

        buy_sell_ratio = launch_data.buy_count / max(launch_data.sell_count, 1)
        unique_wallets = len(launch_data.unique_wallets)

        if launch_data.buy_count == 0 and unique_wallets == 0:
            return

        red_flags = []
        red_flag_penalty = 0.0

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

        whale_data = None
        try:
            whale_data = await self.helius.get_whale_transactions(address, config.whale_min_sol)
            if whale_data and whale_data.get("whale_buys", 0) > 0:
                logger.debug(f"🐋 Whale buys: {whale_data['whale_buys']} for {launch_data.symbol}")
        except Exception as e:
            logger.debug(f"Whale data error: {e}")

        safety_data = None
        try:
            holders_data = await self.birdeye.get_top_holders(address)
            if holders_data:
                safety_data = {"top10_holder_pct": holders_data.get("top10_holder_pct", 0)}
        except Exception as e:
            logger.debug(f"Safety data error: {e}")

        if social_score < 0.1:
            red_flags.append("⚠️ No social presence")
            red_flag_penalty += 0.15

        bonding_boost = 0.0
        bonding_reasons = []

        if age < 60:
            if launch_data.buy_count >= 8 and buy_sell_ratio >= 4.0 and unique_wallets >= 5:
                bonding_boost += 0.25
                bonding_reasons.append("🔥 Strong early frenzy")
            if unique_wallets >= 5 and launch_data.buy_count >= 10:
                bonding_boost += 0.20
                bonding_reasons.append("👥 Multi-wallet demand (5+ wallets)")
            elif launch_data.buy_count >= 5 and buy_sell_ratio >= 3.0:
                bonding_boost += 0.10
                bonding_reasons.append("📈 Early buy pressure")
        elif age < 180:
            if launch_data.buy_count >= 15 and buy_sell_ratio >= 3.0 and unique_wallets >= 5:
                bonding_boost += 0.20
                bonding_reasons.append("📈 Strong buy pressure (verified)")
            if unique_wallets >= 8:
                bonding_boost += 0.15
                bonding_reasons.append("🌐 Diverse buyers (8+ wallets)")
            elif unique_wallets >= 5:
                bonding_boost += 0.10
                bonding_reasons.append("👥 Growing wallets")
        elif age < 300:
            if launch_data.buy_count >= 20 and buy_sell_ratio >= 2.5 and unique_wallets >= 8:
                bonding_boost += 0.15
                bonding_reasons.append("📊 Sustained demand")
            if unique_wallets >= 10:
                bonding_boost += 0.10
                bonding_reasons.append("👥 Growing community")

        if launch_data.holders >= 5 and launch_data.holders <= 100:
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
                        bonding_reasons.append("⚡ Above avg velocity")
                        bonding_boost += 0.15
                else:
                    if launch_data.buy_velocity >= 5:
                        bonding_boost += 0.15
                        bonding_reasons.append("⚡ High velocity")
                    elif launch_data.buy_velocity >= 2:
                        bonding_boost += 0.10
                        bonding_reasons.append("⚡ Active buying")
            except Exception:
                pass

        if launch_data.curve_fill_pct > 0:
            if launch_data.curve_fill_pct >= 80:
                bonding_boost += 0.15
                bonding_reasons.append("📈 Curve nearly full")
            elif launch_data.curve_fill_pct >= 60:
                bonding_boost += 0.10
                bonding_reasons.append("📈 Curve filling fast")
            elif launch_data.curve_fill_pct >= 40:
                bonding_boost += 0.05
                bonding_reasons.append("📈 Curve progressing")

        if bonding_boost == 0 and launch_data.buy_count >= 3:
            bonding_boost += 0.05
            bonding_reasons.append("📊 Minimum activity")

        logger.info(
            f"pre-mig eval {launch_data.symbol}: age={int(age)}s "
            f"buys={launch_data.buy_count} sells={launch_data.sell_count} "
            f"unique={unique_wallets} holders={launch_data.holders} "
            f"ai={ai_score:.2f} soc={social_score:.2f} bond={bonding_boost:.2f} "
            f"red={red_flag_penalty:.2f} "
            f"buy_sell_ratio={buy_sell_ratio:.1f} curve={launch_data.curve_fill_pct:.0f}%"
        )

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
            social_score=social_score, age_seconds=age,
            whale_data=whale_data, safety_data=safety_data
        )

        final_score += bonding_boost
        final_score -= red_flag_penalty
        final_score = max(0.0, final_score)

        effective_threshold = max(threshold, self.filter_engine.min_threshold)
        effective_threshold += red_flag_penalty * 0.5

        logger.info(
            f"pre-mig {launch_data.symbol}: age={int(age)}s "
            f"buys={launch_data.buy_count} sells={launch_data.sell_count} "
            f"ai={ai_score:.2f} soc={social_score:.2f} bond={bonding_boost:.2f} "
            f"red={red_flag_penalty:.2f} final={final_score:.2f} thr={effective_threshold:.2f} "
            f"signal={should_signal} ({filter_reason})"
        )

        if red_flags and red_flag_penalty >= 0.8:
            logger.info(f"🚫 {launch_data.symbol}: Blocked by red flags: {red_flags}")
            return

        if final_score >= effective_threshold:
            momentum_ok, momentum_reason = await self._check_momentum(address, launch_data)
            if not momentum_ok:
                logger.info(f"🚫 {launch_data.symbol}: Blocked by momentum check - {momentum_reason}")
                return
            symbol = launch_data.symbol
            name = launch_data.name
            confidence_pct = int(final_score * 100)
            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
            link = gmgn_link(address)
            social_pct = int(social_score * 100)
            bonding_text = "\n".join([f" • {r}" for r in bonding_reasons]) if bonding_reasons else " • Standard scoring"
            velocity_text = f"⚡ {launch_data.buy_velocity:.1f} buys/min" if launch_data.buy_velocity > 0 else ""
            curve_text = f"📈 Curve: ~{launch_data.curve_fill_pct:.0f}%" if launch_data.curve_fill_pct > 0 else ""

            avg_time_between = ""
            if len(launch_data.buy_timestamps) >= 2:
                diffs = [launch_data.buy_timestamps[i+1] - launch_data.buy_timestamps[i]
                         for i in range(len(launch_data.buy_timestamps)-1)]
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
                f"⏱️ বয়স: <b>{int(age//60)}m {int(age%60)}s</b>\n"
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
            logger.info(f"⚡ প্রি-মাইগ্রেশন সিগন্যাল: {symbol} স্কোর: {final_score:.2f}")

    async def _check_momentum(self, address: str, launch_data=None):
        """Check price/buy momentum. Returns (ok, reason)."""
        try:
            pair = await self.dex.fetch_pair_data(address)
            if not pair:
                if launch_data:
                    bsr = launch_data.buy_count / max(launch_data.sell_count, 1)
                    if bsr < 1.0:
                        return False, f"No pair, buy/sell={bsr:.1f}"
                return True, "No pair data"

            price_change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
            price_change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)

            if price_change_5m < -15:
                return False, f"5m dump: {price_change_5m:+.1f}%"
            if price_change_5m < 0 and price_change_1h < -10:
                return False, f"5m={price_change_5m:+.1f}% 1h={price_change_1h:+.1f}%"
            if launch_data and launch_data.ath_price > 0:
                current_price = float(pair.get("priceUsd", 0) or 0)
                if current_price > 0:
                    ath_drop = ((launch_data.ath_price - current_price) / launch_data.ath_price) * 100
                    if ath_drop > 40:
                        return False, f"ATH drop {ath_drop:.0f}%"
        except Exception as e:
            logger.debug(f"Momentum check error: {e}")
        return True, "OK"

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
                ath_price=price,
                migration_time=launch_data.migration_time,
            )
            await self.state.add_tracked_coin(address, tracked)
            logger.info(f"✅ মাইগ্রেশন ট্র্যাক: {symbol} | price: ${price:.8f}")

            launch_pattern = {
                "buy_count": launch_data.buy_count,
                "sell_count": launch_data.sell_count,
                "unique_wallets": len(launch_data.unique_wallets),
                "volume": launch_data.volume,
                "buy_sell_ratio": launch_data.buy_count / max(launch_data.sell_count, 1),
                "buy_velocity": getattr(launch_data, 'buy_velocity', 0),
                "curve_fill_pct": getattr(launch_data, 'curve_fill_pct', 0),
                "avg_buy_gap": 0,
                "symbol": symbol,
                "address": address,
                "source": "migration_snapshot",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await self.state.save_migration_launch_pattern(address, launch_pattern)

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
                            ath_price=price,
                        )
                        await self.state.add_tracked_coin(addr, tracked)

                # Pre-migration scan
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
                                buys_1h = int(h1.get("buys", 0) or 0)
                                sells_1h = int(h1.get("sells", 0) or 0)
                                vol_1h = float((pair.get("volume") or {}).get("h1", 0) or 0)
                                real_mcap = float(pair.get("fdv", 0) or 0)
                                real_liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)

                                self._last_pre_mig_pair_mcap = real_mcap
                                self._last_pre_mig_pair_liq = real_liq

                                unique_from_txns = buys_1h + sells_1h
                                if len(ld.unique_wallets) > ld.holders:
                                    ld.holders = len(ld.unique_wallets)

                                if vol_1h > ld.volume:
                                    ld.volume = vol_1h
                                    pair_price = float(pair.get("priceUsd", 0) or 0)
                                    if pair_price > 0 and pair_price > ld.ath_price:
                                        ld.ath_price = pair_price
                                    await self.check_pre_migration_signal(addr)
                        await asyncio.sleep(0.3)

                # Tracked coins scan
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

                    if current_price > coin_info.ath_price:
                        coin_info.ath_price = current_price

                    ld = await self.state.get_launch_tracking(addr)
                    if ld and current_price > ld.ath_price:
                        ld.ath_price = current_price
                        await self.state.add_launch_tracking(addr, ld)

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

                    if 0 < age <= 21600:
                        verified, actual_multi = verify_pump(pair, config.pump_multiplier,
                            ath_price=coin_info.ath_price, initial_price=coin_info.initial_price)
                        if verified:
                            await self.state.add_pump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            txs = await self.helius.get_launch_transactions(addr)
                            launch_pat = extract_launch_pattern(txs) if txs else None
                            if not launch_pat:
                                launch_pat = await self.state.get_migration_launch_pattern(addr)
                            learned, learn_msg = learn_pump_with_launch(
                                {"name": name, "symbol": symbol}, pair, actual_multi,
                                launch_pat, addr, manual=False, verified_multiplier=actual_multi
                            )
                            if learned:
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
                        elif actual_multi <= 5.0:
                            await self.state.add_dump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            learn_dump({"name": name, "symbol": symbol}, pair, addr, manual=False)
                        else:
                            logger.info(f"⏭️ Skip {symbol}: {actual_multi}x (5x-8x zone)")
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

                        vol_24h = float((pair.get("volume") or {}).get("h24", 0) or 0)
                        pattern = {
                            "mcap": mcap,
                            "liquidity": liquidity,
                            "vol_liq_ratio": vol_24h / max(liquidity, 1) if liquidity > 0 else 0,
                            "buy_sell_ratio": launch_data.buy_count / max(launch_data.sell_count, 1) if launch_data else 1,
                        }

                        social_score = 0.0
                        try:
                            social_score, _ = await self.social.calculate_social_score(addr, symbol)
                            if not isinstance(social_score, (int, float)):
                                social_score = 0.0
                        except Exception as e:
                            logger.debug(f"Social score error: {e}")

                        whale_data = None
                        try:
                            whale_data = await self.helius.get_whale_transactions(addr, config.whale_min_sol)
                        except Exception:
                            pass

                        safety_data = None
                        try:
                            holders_data = await self.birdeye.get_top_holders(addr)
                            if holders_data:
                                safety_data = {"top10_holder_pct": holders_data.get("top10_holder_pct", 0)}
                        except Exception:
                            pass

                        should_signal, final_score, filter_reason = self.filter_engine.should_signal(
                            addr, pattern, ai_score=ai_score,
                            social_score=social_score, age_seconds=age,
                            whale_data=whale_data, safety_data=safety_data
                        )

                        effective_threshold = max(threshold, self.filter_engine.min_threshold)

                        if should_signal and ai_score >= effective_threshold:
                            momentum_ok, momentum_reason = await self._check_momentum(addr, launch_data)
                            if not momentum_ok:
                                logger.info(f"🚫 {symbol}: Blocked by momentum check - {momentum_reason}")
                                continue
                            holders = coin_info.holders
                            lp = coin_info.lp_locked
                            confidence_pct = int(final_score * 100)
                            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
                            social_pct = int(social_score * 100)

                            await send_msg(self.telegram_app.bot,
                                f"⚡ <b>আর্লি সিগন্যাল!</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🏷️ <b>{name}</b> (${symbol})\n"
                                f"🎯 কনফিডেন্স: {confidence_bar} <b>{confidence_pct}%</b> (থ্রেশোল্ড {int(effective_threshold*100)}%)\n"
                                f"🧠 <i>{reason}</i>\n"
                                f"💵 দাম: <b>{current_price:.8f}</b>\n"
                                f"💰 MCap: <b>{format_number(mcap)}</b>\n"
                                f"💧 লিকুইডিটি: <b>{format_number(liquidity)}</b>\n"
                                f"👥 হোল্ডার: <b>{holders}</b>\n"
                                f"🔒 LP লক: <b>{lp}%</b>\n"
                                f"🌐 Social: <b>{social_pct}%</b>\n"
                                f"⏱️ বয়স: <b>{int(age//60)}m {int(age%60)}s</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🔗 <a href='{link}'>GMGN</a>"
                            )

                            record_signal(addr, symbol, final_score, current_price, mcap,
                                          launch_time=coin_info.launch_time or now_ts,
                                          is_pre_migration=False,
                                          is_pre_migration_known=True,
                                          migration_time=coin_info.launch_time or now_ts)
                            from bot_state import SignalInfo
                            await self.state.add_signal(addr, SignalInfo(
                                symbol=symbol,
                                price_at_signal=current_price,
                                signal_time=now_ts,
                                launch_time=coin_info.launch_time or now_ts,
                                is_pre_migration=False,
                                is_pre_migration_known=True,
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
                                            f"🎯 TP: ${pos.tp_price:.8f} ({((pos.tp_price/pos.entry_price)-1)*100:+.0f}%)\n"
                                            f"🛑 SL: ${pos.sl_price:.8f} ({((pos.sl_price/pos.entry_price)-1)*100:+.0f}%)\n"
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

    async def curve_refresh_loop(self):
        while True:
            try:
                await asyncio.sleep(45)
                tracked = await self.state.get_all_tracked()
                if not tracked:
                    continue
                sem = asyncio.Semaphore(3)
                async def _refresh(addr, ld):
                            async with sem:
                                try:
                                    bc = await self.helius.get_bonding_curve_state(addr)
                                    if not bc:
                                        return
                                    new_progress = float(bc.get("progress_pct", 0) or 0)
                                    is_complete = bool(bc.get("complete", False))
                                    updated = False
                                    now = datetime.now(timezone.utc).timestamp()
                                    if new_progress > ld.curve_fill_pct:
                                        ld.curve_fill_pct = new_progress
                                        updated = True
                                    if is_complete and not ld.migration_time:
                                        ld.migration_time = now
                                        logger.info(f"🚀 মাইগ্রেশন: {ld.symbol} (curve {new_progress:.0f}%)")
                                        updated = True
                                        if addr in self.state.signals:
                                            sig_info = self.state.signals[addr]
                                            sig_info.is_pre_migration = False
                                            sig_info.migration_time = now
                                    pair = await self.dex.fetch_pair_data(addr)
                                    if pair:
                                        pair_price = float(pair.get("priceUsd", 0) or 0)
                                        if pair_price > 0 and pair_price > ld.ath_price:
                                            ld.ath_price = pair_price
                                            updated = True
                                    if updated:
                                        await self.state.add_launch_tracking(addr, ld)
                                except Exception as e:
                                    logger.debug(f"curve refresh error for {addr}: {e}")
                await asyncio.gather(*[_refresh(addr, ld) for addr, ld in tracked.items()], return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"curve_refresh_loop error: {e}")
                await asyncio.sleep(30)

    async def track_outcomes_loop(self):
        """
        Monitoring-only: ATH + honeypot detection at T+5/15/60.
        NO pre-migration learning. Learning ONLY post-migration via verify_pump().
        """
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
                    if current_price > ld.ath_price:
                        ld.ath_price = current_price
                        await self.state.add_launch_tracking(addr, ld)

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
                        logger.info(f"🍯 লেট-রিভিল হানিপট: {ld.symbol} @ T+{int(next_offset)}s")

                    ld.eval_done[str(next_offset)] = True

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"track_outcomes_loop error: {e}")

            await asyncio.sleep(60)

    async def connection_monitor_loop(self):
        """Monitor internet connectivity and reconnect PumpPortal if needed."""
        while True:
            try:
                await asyncio.sleep(120)
                # Simple connectivity test via DexScreener
                try:
                    test = await self.dex.fetch_new_solana_pairs()
                    if test is not None:
                        if not self._last_internet_ok:
                            logger.info("🌐 Internet restored")
                            self._last_internet_ok = True
                    else:
                        raise Exception("null response")
                except Exception:
                    if self._last_internet_ok:
                        logger.warning("⚠️ Internet connection issue detected")
                        self._last_internet_ok = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"connection_monitor error: {e}")

    async def github_sync_loop(self):
        while True:
            try:
                await asyncio.sleep(config.github_sync_interval)
                await sync_to_github()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"github_sync_loop error: {e}")

    async def backtest_loop(self):
        """Auto-run backtest every 7 days to keep model fresh."""
        await asyncio.sleep(3600)  # Wait 1h after start
        while True:
            try:
                engine = BacktestEngine(
                    self.session, self.dex, self.helius,
                    lambda t: send_msg(self.telegram_app.bot, t)
                )
                await engine.run(days=7, max_tokens=100)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"backtest_loop error: {e}")
            await asyncio.sleep(7 * 24 * 3600)  # Every 7 days

    async def daily_summary_loop(self):
        """Send daily performance summary."""
        while True:
            try:
                await asyncio.sleep(86400)
                report = get_daily_report()
                stats = get_stats()
                await send_msg(self.telegram_app.bot,
                    f"📊 <b>দৈনিক রিপোর্ট</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📅 তারিখ: <b>{report['date']}</b>\n"
                    f"📡 সিগন্যাল পাঠানো: <b>{report['signals_sent']}</b>\n"
                    f"🚀 পাম্প শেখা: <b>{report['pumps_learned']}</b>\n"
                    f"✅ সফল: <b>{report['successful']}/{report['checked']}</b>\n"
                    f"🎯 Accuracy: <b>{stats['accuracy']}%</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📚 মোট পাম্প: <b>{stats['pump_patterns']}</b> | ডাম্প: <b>{stats['dump_patterns']}</b>"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"daily_summary_loop error: {e}")

    async def paper_trading_loop(self):
        """Monitor paper trading positions and timeout old ones."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 min
                now = datetime.now(timezone.utc).timestamp()
                for pos in self.paper_trader.get_open_positions():
                    addr = pos.address
                    age = now - pos.entry_time
                    if age > 10800:  # 3h timeout
                        pair = await self.dex.fetch_pair_data(addr)
                        current_price = float(pair.get("priceUsd", 0) or 0) if pair else pos.entry_price
                        closed = await self.paper_trader.force_close(addr, current_price)
                        if closed:
                            emoji = "✅" if closed.pnl_sol >= 0 else "❌"
                            await send_msg(self.telegram_app.bot,
                                f"{emoji} <b>Paper Timeout Close (3h)</b>\n"
                                f"🏷️ ${pos.symbol}\n"
                                f"💰 PnL: <b>{closed.pnl_sol:+.4f} SOL ({closed.pnl_pct:+.1f}%)</b>\n"
                                f"💵 ব্যালেন্স: {self.paper_trader.state.current_sol:.4f} SOL"
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"paper_trading_loop error: {e}")

    async def health_check_loop(self):
        """Self-healing: scan logs, detect errors, auto-fix common issues."""
        import re, os
        log_file = os.environ.get("LOG_FILE", "").strip()
        if not log_file:
            log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log")
        last_pos = 0
        error_counts = {}
        fixed_count = 0
        env_fix_applied = False
        while True:
            try:
                await asyncio.sleep(300)
                if not os.path.exists(log_file):
                    continue
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    last_pos = f.tell()

                fixes = []
                for line in new_lines:
                    if "ERROR" in line or "Traceback" in line:
                        err_key = line[:80]
                        error_counts[err_key] = error_counts.get(err_key, 0) + 1

                    if "Smart-merge step failed" in line and "list" in line:
                        fixes.append("smart_merge_list")

                    if "Duplicate" in line.lower() or (new_lines.count(line) > 1 and line.strip()):
                        fixes.append("duplicate_log")

                if not env_fix_applied and os.environ.get("LOG_FILE", "").strip():
                    try:
                        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
                        if os.path.exists(env_path):
                            with open(env_path, "r") as f:
                                lines = f.readlines()
                            new_lines = [l for l in lines if not l.strip().startswith("LOG_FILE=")]
                            if len(new_lines) < len(lines):
                                with open(env_path, "w") as f:
                                    f.writelines(new_lines)
                                env_fix_applied = True
                                fixes.append("env_log_file_removed")
                    except Exception as e:
                        logger.debug(f"auto-fix env LOG_FILE failed: {e}")

                if "smart_merge_list" in fixes:
                    try:
                        data_file = os.environ.get("DATA_FILE", "bot_data.json")
                        if os.path.exists(data_file):
                            import json
                            with open(data_file) as f:
                                data = json.load(f)
                            ta = data.get("trained_addresses", [])
                            if isinstance(ta, list) and ta and isinstance(ta[0], dict):
                                data["trained_addresses"] = [a.get("address", str(a)) for a in ta]
                                with open(data_file, "w") as f:
                                    json.dump(data, f, indent=2)
                                fixed_count += 1
                                fixes = [f for f in fixes if f != "smart_merge_list"]
                    except Exception as e:
                        logger.debug(f"auto-fix smart_merge failed: {e}")

                if fixes:
                    unique_fixes = list(set(fixes))
                    await send_msg(self.telegram_app.bot,
                        f"🔧 <b>Auto-Fix Applied</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"Fixed: {', '.join(unique_fixes)}\n"
                        f"Total fixes: {fixed_count}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"✅ Bot running: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                    )

                if error_counts:
                    top_errors = sorted(error_counts.items(), key=lambda x: -x[1])[:3]
                    report = "\n".join([f"  ⚠️ ({c}x) {e[:60]}" for e, c in top_errors])
                    await send_msg(self.telegram_app.bot,
                        f"🩺 <b>Health Check</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"{report}\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"✅ Bot running: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                    )
                    error_counts.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"health_check_loop error: {e}")

    async def feature_request_loop(self):
        """Listen for /feature requests from Telegram and save to file."""
        import os
        feature_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_requests.jsonl")
        while True:
            try:
                await asyncio.sleep(10)
                if not os.path.exists("/tmp/feature_request.txt"):
                    continue
                with open("/tmp/feature_request.txt", "r") as f:
                    request = f.read().strip()
                os.remove("/tmp/feature_request.txt")
                if not request:
                    continue
                import json
                entry = {
                    "request": request,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "pending"
                }
                with open(feature_file, "a") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                await send_msg(self.telegram_app.bot,
                    f"📝 <b>ফিচার রিকোয়েস্ট সেভ হয়েছে!</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💬 {request[:200]}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"⏳ স্ট্যাটাস: pending\n"
                    f"🤖 AI assistant এটি implement করবে।"
                )
                logger.info(f"📝 Feature request saved: {request[:100]}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"feature_request_loop error: {e}")


def main():
    bot = MemeBot()

    def handle_signal(signum, frame):
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.ensure_future(bot.shutdown())
        )

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
