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
    extract_launch_features, record_launch, check_and_record_outcome,
    match_pump_patterns, match_dump_patterns, record_signal_result,
    get_stats, get_daily_report,
    get_launch_age, is_duplicate, purge_honeypot_patterns,
    save_honeypot_blocklist, load_honeypot_blocklist,
    load_data, save_data, PUMP_THRESHOLD, DUMP_THRESHOLD,
    record_missed_pump, auto_learn_update, get_signal_criteria,
    compute_signal_criteria, learn_divergence_point,
    get_bad_hours, get_good_hours, get_hourly_stats_report
)
from github_sync import sync_to_github, restore_from_github
from utils import format_number, gmgn_link, dexscreener_link, setup_logging
from backtest import BacktestEngine, REPORTS_DIR, MAX_REPORTS
from social_signals import SocialSignalEngine
from honeypot_detector import HoneypotDetector
from paper_trader import get_paper_trader
import os

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chat_id")
CHANNEL_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".channel_id")

logger = setup_logging("meme_bot")


async def send_msg(bot: Bot, text: str) -> None:
    try:
        chat_id = config.chat_id
        if not chat_id or chat_id == "0":
            if os.path.exists(CHAT_ID_FILE):
                with open(CHAT_ID_FILE) as f:
                    chat_id = f.read().strip()
        if not chat_id or chat_id == "0":
            logger.warning("No chat_id configured yet. Message the bot first!")
            return
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Send error: {e}")

async def send_signal(bot: Bot, text: str, address: str = "") -> None:
    """Send ONLY address to channel for scraper, full details to user."""
    channel_id = config.channel_id
    if not channel_id and os.path.exists(CHANNEL_ID_FILE):
        try:
            with open(CHANNEL_ID_FILE) as f:
                channel_id = f.read().strip()
                config.channel_id = channel_id
        except Exception:
            pass
    if channel_id:
        try:
            await bot.send_message(
                chat_id=channel_id, text=address,
                disable_web_page_preview=True
            )
            logger.info(f"📤 Channel signal sent: {address[:20]}...")
        except Exception as e:
            logger.error(f"Channel send error: {e}")
    await send_msg(bot, text)

async def send_maestro(bot: Bot, address: str) -> None:
    """Send token address to channel for Maestro auto-trade."""
    channel_id = config.channel_id
    if not channel_id and os.path.exists(CHANNEL_ID_FILE):
        try:
            with open(CHANNEL_ID_FILE) as f:
                channel_id = f.read().strip()
                config.channel_id = channel_id
        except Exception:
            pass
    if not channel_id:
        return
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=address,
            disable_web_page_preview=True
        )
        logger.info(f"📤 Channel: {address[:8]}...")
    except Exception as e:
        logger.debug(f"Channel send error: {e}")


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
        self.honeypot: HoneypotDetector = None
        self.pumpportal: PumpPortalWS = None
        self.telegram_app: Application = None
        self.handlers: TelegramHandlers = None
        self.paper_trader = get_paper_trader()
        self._shutdown_event = asyncio.Event()
        self._tasks: list = []
        self._last_internet_ok: bool = True
        self._telegram_started = False
        self._telegram_retries = 0

    def _telegram_error_handler(self, update, context):
        error = context if isinstance(context, Exception) else getattr(context, 'error', context)
        logger.error(f"Telegram error: {error}", exc_info=error if isinstance(error, Exception) else None)
        if "Conflict" in str(error):
            self._telegram_retries += 1
            if self._telegram_retries > 5:
                logger.warning("Too many 409 conflicts, assuming another instance is running")

    async def start(self):
        self.session = aiohttp.ClientSession()
        self.dex = DexScreenerClient(self.session)
        self.rugcheck = RugcheckClient(self.session)
        self.helius = HeliusClient(self.session)
        self.birdeye = BirdeyeClient(self.session, config.birdeye_api_key)
        self.jupiter = JupiterClient(self.session)
        self.social = SocialSignalEngine(self.session)
        self.honeypot = HoneypotDetector(self.session, rugcheck=self.rugcheck, helius=self.helius, dex=self.dex, birdeye=self.birdeye)
        self.pumpportal = PumpPortalWS(
            on_new_token=self._on_new_token,
            on_migration=self._on_migration,
            on_trade=self._on_trade
        )

        self.telegram_app = Application.builder().token(config.bot_token).build()
        self.handlers = TelegramHandlers(self.state, self.dex, self.session, self.paper_trader)
        register_handlers(self.telegram_app, self.handlers)

        await restore_from_github()

        try:
            hp_set, dep_set, alerted_set = load_honeypot_blocklist()
            for a in hp_set:
                await self.state.mark_honeypot(a)
            for d in dep_set:
                await self.state.add_blocked_deployer(d)
            async with self.state._lock:
                self.state.alerted_coins.update(alerted_set)
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
            asyncio.create_task(self.signal_confirmation_loop(), name="signal_confirm"),
            asyncio.create_task(self.track_outcomes_loop(), name="track_outcomes"),
            asyncio.create_task(self.connection_monitor_loop(), name="conn_monitor"),
        ]

        if config.enable_github_sync:
            self._tasks.append(asyncio.create_task(self.github_sync_loop(), name="github_sync"))
            self._tasks.append(asyncio.create_task(self.backtest_loop(), name="backtest"))
        self._tasks.append(asyncio.create_task(self.continuous_learn_loop(), name="continuous_learn"))
        self._tasks.append(asyncio.create_task(self.daily_summary_loop(), name="daily_summary"))
        self._tasks.append(asyncio.create_task(self.auto_learn_loop(), name="auto_learn"))

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
        self._tasks.append(asyncio.create_task(self.lp_monitoring_loop(), name="lp_monitoring"))

        # Pump collector: background 24/7 data collection from DexScreener
        try:
            from pump_collector import pump_collector_loop
            self._tasks.append(asyncio.create_task(pump_collector_loop(self), name="pump_collector"))
            logger.info("🔄 Pump collector loop started")
        except Exception as e:
            logger.debug(f"Pump collector start error: {e}")

        await send_msg(self.telegram_app.bot, "🤖 <b>বট v3 চালু!</b>\n✅ 5x filter + Auto-verify + Social signals + Paper Trading সক্রিয়")

        try:
            self.telegram_app.add_error_handler(self._telegram_error_handler)
            await self.telegram_app.initialize()
            await self.telegram_app.start()

            # Retry polling start on Conflict errors (another instance)
            for attempt in range(10):
                try:
                    await self.telegram_app.updater.start_polling(
                        allowed_updates=["message", "callback_query"],
                    )
                    break
                except Exception as e:
                    if "Conflict" in str(e) and attempt < 9:
                        logger.warning(f"Telegram Conflict (attempt {attempt+1}/10), retrying in 5s...")
                        await asyncio.sleep(5)
                    else:
                        raise

            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
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
            try:
                await self.pumpportal.close()
            except Exception:
                pass

        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass

        if self.telegram_app:
            try:
                await self.telegram_app.updater.stop()
                await self.telegram_app.stop()
                await self.telegram_app.shutdown()
            except Exception as e:
                logger.error(f"Telegram shutdown error: {e}")

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

        # Large sell detection → instant alert + Maestro sell
        if tx_type == "sell" and amount >= 0.5:
            symbol = launch_data.symbol if launch_data else address[:6]
            name = launch_data.name if launch_data else symbol
            logger.warning(f"🔴 LARGE SELL: {symbol} {amount:.2f} SOL by {wallet[:8]}...")
            await send_msg(self.telegram_app.bot,
                f"🔴 <b>বড় সেল সতর্কতা!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🏷️ <b>{name}</b> (${symbol})\n"
                f"💰 Sell: <b>{amount:.2f} SOL</b>\n"
                f"🔗 GMGN: {gmgn_link(address)}\n"
                f"🔗 DexScreener: {dexscreener_link(address)}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>দ্রুত বিক্রি করো!</b>"
            )
            await send_maestro(self.telegram_app.bot, address)

        await self.check_pre_migration_signal(address)

    async def process_new_token(self, data: dict):
        address = data.get("mint")
        if not address:
            return
        if await self.state.is_blacklisted(address):
            logger.debug(f"[SKIP] {data.get('symbol','?')} blacklisted")
            return
        if await self.state.is_honeypot(address):
            logger.debug(f"[SKIP] {data.get('symbol','?')} honeypot")
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
        if len(existing_tokens) > 1:
            await self.state.add_blocked_deployer(deployer)
            logger.info(f"🚫 Serial deployer blocked: {symbol} deployer {deployer[:8]}... has {len(existing_tokens)} tokens")
            return

        if deployer:
            try:
                history = await self.helius.get_deployer_history(deployer)
                if history.get("total_launches", 0) > 3:
                    await self.state.add_blocked_deployer(deployer)
                    logger.info(f"🚫 Deployer blocked (history): {symbol} deployer {deployer[:8]}... created {history['total_launches']} tokens")
                    return
            except Exception:
                pass

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

        if holders is not None and holders < 20:
            logger.info(f"[SKIP] {symbol}: holders={holders} < 20 (dump filter)")
            return

        age = datetime.now(timezone.utc).timestamp() - launch_data.launch_time
        symbol = launch_data.symbol

        # Only skip during very bad hours (< 50% pump rate)
        try:
            bad_hours = get_bad_hours(min_signals=3, max_win_rate=0.50)
        except Exception:
            bad_hours = set()
        current_hour = datetime.now(timezone.utc).hour
        if bad_hours and current_hour in bad_hours:
            logger.info(f"[SKIP] {symbol}: hour {current_hour}:00 UTC — bad hour")
            return

        if age < 30:
            return

        buy_sell_ratio = launch_data.buy_count / max(launch_data.sell_count, 1)
        unique_wallets = len(launch_data.unique_wallets)

        if launch_data.buy_count == 0 and unique_wallets == 0:
            return

        if await self.state.is_alerted(address):
            return

        pair_data = None
        try:
            pair_data = await self.dex.fetch_pair_data(address)
        except Exception:
            pass

        if not pair_data:
            return

        liquidity = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        mcap = float(pair_data.get("fdv", 0) or 0)
        price_usd = float(pair_data.get("priceUsd", 0) or 0)

        # Volume spike detection from DexScreener data
        h1_buys = int(((pair_data.get("txns") or {}).get("h1") or {}).get("buys", 0) or 0)
        h1_sells = int(((pair_data.get("txns") or {}).get("h1") or {}).get("sells", 0) or 0)
        buys_5m = int(((pair_data.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
        sells_5m = int(((pair_data.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
        avg_5m_buys = max(h1_buys / 12, 1)
        volume_spike = buys_5m / avg_5m_buys if avg_5m_buys > 0 else 0

        # Relaxed thresholds when volume spike detected
        mcap_threshold = 2000 if volume_spike >= 3.0 else 3000
        bsr_threshold = 1.2 if volume_spike >= 3.0 else 1.7

        if mcap < mcap_threshold:
            logger.info(f"[SKIP] {symbol}: mcap ${mcap:.0f} < ${mcap_threshold} (spike={volume_spike:.1f}x)")
            return
        if buy_sell_ratio < bsr_threshold:
            logger.info(f"[SKIP] {symbol}: bsr={buy_sell_ratio:.2f} < {bsr_threshold} (spike={volume_spike:.1f}x)")
            return

        real_holders = launch_data.holders
        try:
            h = await self.helius.get_holder_count(address)
            if h is not None and h > 0:
                real_holders = h
                launch_data.holders = h
            elif launch_data.holders == 0:
                real_holders = len(launch_data.unique_wallets)
                launch_data.holders = real_holders
        except Exception:
            if launch_data.holders == 0:
                real_holders = len(launch_data.unique_wallets)
                launch_data.holders = real_holders

        if launch_data.lp_locked == 0:
            try:
                rug = await self.rugcheck.check_token(address, symbol)
                if rug and hasattr(rug, "risks"):
                    import re
                    for r in rug.risks:
                        if "LP" in r:
                            m = re.search(r"(\d+)%", r)
                            if m:
                                launch_data.lp_locked = int(m.group(1))
                                break
            except Exception:
                pass

        if real_holders < 20:
            logger.info(f"[SKIP] {symbol}: holders={real_holders} < 20")
            return

        if deployer := launch_data.deployer_wallet:
            if await self.state.is_deployer_blocked(deployer):
                logger.info(f"[SKIP] {symbol}: deployer {deployer[:8]}... blocked (bundle)")
                return
            existing = await self.state.get_deployer_tokens(deployer)
            if len(existing) >= 2:
                logger.info(f"[SKIP] {symbol}: deployer {deployer[:8]}... has {len(existing)} tokens (bundle)")
                await self.state.add_blocked_deployer(deployer)
                return

        features = extract_launch_features(
            launch_data, pair_data=pair_data, unique_wallets=unique_wallets
        )
        features["launch_time"] = launch_data.launch_time
        features["liquidity"] = liquidity
        features["mcap"] = mcap

        record_launch(address, symbol, features)

        match, match_score, match_reason = match_pump_patterns(features, min_similarity=0.60)

        # Check if token matches known dump patterns — reject if moderate+ confidence
        is_dump, dump_score, dump_reason = match_dump_patterns(features, min_similarity=0.70)
        if is_dump:
            logger.info(f"[SKIP] {symbol}: dump pattern match ({dump_score:.0%}) — {dump_reason}")
            return

        if not match:
            criteria = get_signal_criteria()
            h_score = 0.0
            h_reasons = []
            effective_wallets = min(real_holders, unique_wallets) if real_holders > 0 else unique_wallets

            # Buy velocity: buys per minute (must have enough data)
            buy_velocity = 0
            if age > 60:
                buy_velocity = launch_data.buy_count / (age / 60)

            # Buy count: need high activity
            if launch_data.buy_count >= 30:
                h_score += 0.30
                h_reasons.append(f"buys={launch_data.buy_count}")
            elif launch_data.buy_count >= 15:
                h_score += 0.20
                h_reasons.append(f"buys={launch_data.buy_count}")
            elif launch_data.buy_count >= 8:
                h_score += 0.10
                h_reasons.append(f"buys={launch_data.buy_count}")

            # Buy velocity: must be actively buying (not old token with many buys)
            if buy_velocity >= 5:
                h_score += 0.25
                h_reasons.append(f"vel={buy_velocity:.1f}/min")
            elif buy_velocity >= 2:
                h_score += 0.15
                h_reasons.append(f"vel={buy_velocity:.1f}/min")
            elif buy_velocity >= 1:
                h_score += 0.08
                h_reasons.append(f"vel={buy_velocity:.1f}/min")

            # Wallets: real unique buyers
            if effective_wallets >= 50:
                h_score += 0.20
                h_reasons.append(f"wallets={effective_wallets}")
            elif effective_wallets >= 20:
                h_score += 0.12
                h_reasons.append(f"wallets={effective_wallets}")
            elif effective_wallets >= 10:
                h_score += 0.06
                h_reasons.append(f"wallets={effective_wallets}")

            # BSR: buying pressure
            if buy_sell_ratio >= 2.0:
                h_score += 0.15
                h_reasons.append(f"bsr={buy_sell_ratio:.1f}")
            elif buy_sell_ratio >= 1.5:
                h_score += 0.10
                h_reasons.append(f"bsr={buy_sell_ratio:.1f}")
            elif buy_sell_ratio >= 1.2:
                h_score += 0.05
                h_reasons.append(f"bsr={buy_sell_ratio:.1f}")

            # Holders: real holders from Helius
            if real_holders >= 30:
                h_score += 0.15
                h_reasons.append(f"holders={real_holders}")
            elif real_holders >= 10:
                h_score += 0.10
                h_reasons.append(f"holders={real_holders}")
            elif real_holders >= 3:
                h_score += 0.05
                h_reasons.append(f"holders={real_holders}")

            # LP locked scoring
            lp_locked = launch_data.lp_locked
            if lp_locked >= 80:
                h_score += 0.10
                h_reasons.append(f"lp={lp_locked}%")
            elif lp_locked < 50 and lp_locked > 0:
                h_score -= 0.10
                h_reasons.append(f"lp_low={lp_locked}%")

            criteria = get_signal_criteria()
            h_threshold = min(criteria.get("heuristic_threshold", 0.60), 0.50)
            if h_score >= h_threshold:
                match = True
                match_score = h_score
                match_reason = "Heuristic: " + " ".join(h_reasons)

        logger.info(
            f"[EVAL] {symbol}: age={int(age)}s buys={launch_data.buy_count} "
            f"bsr={buy_sell_ratio:.1f} wallets={unique_wallets} "
            f"liq=${int(liquidity)} holders={real_holders} mcap={format_number(mcap)} "
            f"→ match={match:.0%} {match_reason}"
        )

        if not match:
            return

        if address in self.state.pending_signals:
            return

        is_pre_migration = launch_data.migration_time == 0
        if not is_pre_migration and liquidity < 500:
            logger.info(f"[SKIP] {symbol}: signal rejected — liq=${int(liquidity)} < $500")
            return
        min_mcap = 500 if is_pre_migration else 5000
        if mcap < min_mcap:
            logger.info(f"[SKIP] {symbol}: signal rejected — mcap={format_number(mcap)} < ${min_mcap}")
            return
        if real_holders < 20:
            logger.info(f"[SKIP] {symbol}: signal rejected — holders={real_holders} < 20")
            return

        try:
            deployer = launch_data.deployer_wallet or ""
            hp = await self.honeypot.check(address, symbol, deployer=deployer)
            if hp and hp.is_honeypot:
                logger.info(f"[SKIP] {symbol}: honeypot detected — {hp.reasons[:2]}")
                await self.state.add_blacklisted(address)
                return

            # LP provider scoring
            lp_count = hp.lp_providers_count if hp else 0
            dep_has_lp = hp.deployer_has_lp if hp else False
            if dep_has_lp:
                h_score -= 0.20
                h_reasons.append("deployer_has_lp")
                logger.info(f"[LP] {symbol}: deployer has LP — high risk")
            elif lp_count >= 3:
                h_score += 0.10
                h_reasons.append(f"lp_providers={lp_count}")
            elif lp_count == 2:
                h_score += 0.02
                h_reasons.append(f"lp_providers={lp_count}")
            elif lp_count == 1:
                h_score -= 0.15
                h_reasons.append("single_lp_provider")
                logger.info(f"[LP] {symbol}: single LP provider — risky")
        except Exception:
            pass

        link = gmgn_link(address)
        confidence_pct = int(match_score * 100)
        confidence_bar = "🟢" * max(1, int(confidence_pct / 20)) + "⚪" * (5 - max(1, int(confidence_pct / 20)))

        from bot_state import PendingSignal
        pending = PendingSignal(
            symbol=symbol,
            address=address,
            name=launch_data.name,
            match_score=match_score,
            match_reason=match_reason,
            price_at_match=price_usd,
            mcap=mcap,
            liquidity=liquidity,
            holders=real_holders,
            unique_wallets=unique_wallets,
            buy_count=launch_data.buy_count,
            sell_count=launch_data.sell_count,
            buy_sell_ratio=buy_sell_ratio,
            lp_locked=launch_data.lp_locked,
            age_seconds=age,
            pending_since=datetime.now(timezone.utc).timestamp(),
            last_check_price=price_usd,
        )
        self.state.pending_signals[address] = pending
        logger.info(f"[PENDING] {symbol}: match={match_score:.0%} — waiting for confirmation "
                     f"(price={price_usd:.8f})")

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
                        holders = 0
                        lp_locked = 0.0
                        deployer = ""
                        try:
                            h = await self.helius.get_holder_count(addr)
                            if h is not None and h > 0:
                                holders = h
                        except Exception:
                            pass
                        try:
                            rug = await self.rugcheck.check_token(addr, pair.get("baseToken", {}).get("symbol", "???"))
                            if rug:
                                lp_locked = rug.lp_locked
                        except Exception:
                            pass
                        tracked = TrackedCoin(
                            initial_price=price,
                            name=pair.get("baseToken", {}).get("name", "Unknown"),
                            symbol=pair.get("baseToken", {}).get("symbol", "???"),
                            first_seen=launch_ts,
                            launch_time=launch_ts,
                            ath_price=price,
                            holders=holders,
                            lp_locked=lp_locked,
                            deployer_wallet=deployer,
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
                                ld.buy_count = max(ld.buy_count, buys_1h)
                                ld.sell_count = max(ld.sell_count, sells_1h)
                                if unique_from_txns > len(ld.unique_wallets):
                                    for i in range(len(ld.unique_wallets), unique_from_txns):
                                        ld.unique_wallets.add(f"dex_{i}")
                                if ld.holders == 0 and len(ld.unique_wallets) > 0:
                                    ld.holders = len(ld.unique_wallets)

                                if vol_1h > ld.volume:
                                    ld.volume = vol_1h
                                    pair_price = float(pair.get("priceUsd", 0) or 0)
                                    if pair_price > 0 and pair_price > ld.ath_price:
                                        ld.ath_price = pair_price

                                if ld.holders == 0:
                                    try:
                                        h = await self.helius.get_holder_count(addr)
                                        if h is not None and h > 0:
                                            ld.holders = h
                                    except Exception:
                                        pass

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

                    # Trailing SL: DISABLED - user wants signals only
                    # if ld and ld.ath_price > 0 and current_price > 0:
                    #     ath_drop_pct = ((ld.ath_price - current_price) / ld.ath_price) * 100
                    #     if ath_drop_pct >= 30 and not ld.trailing_sl_triggered:
                    #         ld.trailing_sl_triggered = True
                    #         await self.state.add_launch_tracking(addr, ld)
                    #         logger.warning(f"🔴 TRAILING SL: {symbol} ATH drop {ath_drop_pct:.0f}%")
                    #         await send_msg(self.telegram_app.bot,
                    #             f"🔴 <b>Trailing SL!</b>\n"
                    #             f"━━━━━━━━━━━━━━━━\n"
                    #             f"🏷️ ${symbol}\n"
                    #             f"📈 ATH: ${ld.ath_price:.8f}\n"
                    #             f"📉 Now: ${current_price:.8f} ({ath_drop_pct:.0f}% drop)\n"
                    #             f"⚠️ <b>বিক্রি করো!</b>"
                    #         )
                    #         await send_maestro(self.telegram_app.bot, addr)

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
                        if mcap >= PUMP_THRESHOLD and not await self.state.is_alerted(addr):
                            await self.state.add_pump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            logger.info(f"🚀 পাম্প কয়েন! {symbol} mcap={format_number(mcap)}")
                            # NO send_signal() here — scraper should NOT buy pump coins
                            # Only learn pattern + track for pre-migration signals
                            await self.state.add_alerted(addr)

                            launch_data = await self.state.get_launch_tracking(addr)
                            if launch_data and not launch_data.pre_signal_sent:
                                features = extract_launch_features(launch_data, pair_data=pair, unique_wallets=len(launch_data.unique_wallets))
                                features["launch_time"] = launch_data.launch_time
                                features["liquidity"] = liquidity
                                features["mcap"] = mcap
                                ath_mult = coin_info.ath_price / coin_info.initial_price if coin_info.initial_price > 0 else 1
                                record_missed_pump(addr, symbol, features, ath_mult)
                                logger.info(f"📚 Missed pump recorded: {symbol} (no pre-mig signal)")
                                # Auto-learn after new data
                                try:
                                    from learner import enhanced_auto_learn
                                    enhanced_auto_learn()
                                except Exception:
                                    pass

                            if config.enable_github_sync:
                                await sync_to_github(f"পাম্প: {symbol} mcap={format_number(mcap)}")
                        elif mcap < 500:
                            await self.state.add_dump_coin(addr, CoinInfo(name=name, symbol=symbol))
                            logger.info(f"📉 ডাম্প কয়েন: {symbol} mcap={format_number(mcap)}")
                            continue
                        elif not await self.state.is_alerted(addr) and 50 <= mcap < 100000:
                            h1_buys = int(((pair or {}).get("txns") or {}).get("h1", {}).get("buys", 0) or 0)
                            h1_sells = int(((pair or {}).get("txns") or {}).get("h1", {}).get("sells", 0) or 0)

                            # Volume spike: current 5m rate vs 1h average
                            buys_5m = int(((pair or {}).get("txns") or {}).get("m5", {}).get("buys", 0) or 0)
                            sells_5m = int(((pair or {}).get("txns") or {}).get("m5", {}).get("sells", 0) or 0)
                            avg_5m_buys = max(h1_buys / 12, 1)
                            volume_spike = buys_5m / avg_5m_buys if avg_5m_buys > 0 else 0
                            buy_sell_5m = buys_5m / max(sells_5m, 1)

                            if h1_buys < 5 and volume_spike < 3.0:
                                logger.info(f"[SKIP] {symbol}: climbing — h1 buys={h1_buys} < 5, spike={volume_spike:.1f}x")
                                continue

                            # Must be < 6 hours old for climbing
                            if age > 21600:
                                logger.info(f"[SKIP] {symbol}: climbing — age {int(age)}s > 6h")
                                continue

                            # HARD FILTERS: relaxed when volume spike detected
                            h1_bsr = h1_buys / max(h1_sells, 1)
                            bsr_threshold = 1.2 if volume_spike >= 3.0 else 1.7
                            if h1_bsr < bsr_threshold:
                                logger.info(f"[SKIP] {symbol}: climbing — bsr={h1_bsr:.2f} < {bsr_threshold} (spike={volume_spike:.1f}x)")
                                continue
                            buys_threshold = 8 if volume_spike >= 3.0 else 15
                            if h1_buys < buys_threshold:
                                logger.info(f"[SKIP] {symbol}: climbing — h1_buys={h1_buys} < {buys_threshold} (spike={volume_spike:.1f}x)")
                                continue

                            pair_data = pair
                            price_change_1h = float(pair_data.get("priceChange", {}).get("h1", 0) or 0)
                            price_change_5m = float(pair_data.get("priceChange", {}).get("m5", 0) or 0)
                            vol_24h = float((pair_data.get("volume") or {}).get("h24", 0) or 0)
                            vol_liq = vol_24h / max(liquidity, 1) if liquidity > 0 else 0
                            buys_5m = int(((pair_data.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
                            sells_5m = int(((pair_data.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
                            buy_sell_5m = buys_5m / max(sells_5m, 1)

                            climbing_score = 0.0
                            climb_reasons = []
                            if price_change_1h > 50:
                                climbing_score += 0.35
                                climb_reasons.append(f"1h +{price_change_1h:.0f}%")
                            elif price_change_1h > 20:
                                climbing_score += 0.2
                                climb_reasons.append(f"1h +{price_change_1h:.0f}%")
                            if price_change_5m > 10:
                                climbing_score += 0.15
                                climb_reasons.append(f"5m +{price_change_5m:.0f}%")
                            if vol_liq >= 0.3:
                                climbing_score += 0.15
                                climb_reasons.append(f"Vol/Liq {vol_liq:.1f}")
                            if buy_sell_5m >= 2.0:
                                climbing_score += 0.15
                                climb_reasons.append(f"B/S {buy_sell_5m:.1f}")
                            if volume_spike >= 3.0:
                                climbing_score += 0.25
                                climb_reasons.append(f"Vol Spike {volume_spike:.1f}x")
                            elif volume_spike >= 2.0:
                                climbing_score += 0.1
                                climb_reasons.append(f"Vol Spike {volume_spike:.1f}x")
                            if buys_5m >= 10:
                                climbing_score += 0.1
                                climb_reasons.append(f"{buys_5m} buys/5m")

                            # Skip only during very bad hours
                            try:
                                bad_hours = get_bad_hours(min_signals=3, max_win_rate=0.50)
                            except Exception:
                                bad_hours = set()
                            current_hour = datetime.now(timezone.utc).hour
                            if bad_hours and current_hour in bad_hours:
                                logger.info(f"[SKIP] {symbol}: climbing — hour {current_hour}:00 UTC bad hour")
                                continue

                            if climbing_score >= 0.40:
                                confidence_pct = int(climbing_score * 100)
                                confidence_bar = "🟢" * max(1, int(confidence_pct/20)) + "⚪" * (5 - max(1, int(confidence_pct/20)))
                                reason_text = ", ".join(climb_reasons[:3])
                                age_min = int(age // 60)
                                age_sec = int(age % 60)

                                await send_signal(self.telegram_app.bot,
                                    f"📈 ক্লাইম্বিং টোকেন!\n"
                                    f"━━━━━━━━━━━━━━━━\n"
                                    f"🏷️ {name} (${symbol})\n"
                                    f"📍 {addr}\n"
                                    f"🎯 কনফিডেন্স: {confidence_bar} {confidence_pct}%\n"
                                    f"🧠 {reason_text}\n"
                                    f"💵 দাম: {current_price:.8f}\n"
                                    f"💰 MCap: {format_number(mcap)}\n"
                                    f"💧 লিকুইডিটি: {format_number(liquidity)}\n"
                                    f"📊 ১h বাই: {h1_buys} | সেল: {h1_sells}\n"
                                    f"🔒 LP লক: {coin_info.lp_locked}%\n"
                                    f"⏱️ বয়স: {age_min}m {age_sec}s\n"
                                    f"━━━━━━━━━━━━━━━━\n"
                                    f"🔗 GMGN: {link}\n"
                                    f"🔗 DexScreener: {dexscreener_link(addr)}",
                                    addr
                                )

                                from bot_state import SignalInfo
                                await self.state.add_signal(addr, SignalInfo(
                                    symbol=symbol,
                                    price_at_signal=current_price,
                                    signal_time=now_ts,
                                    launch_time=coin_info.launch_time or now_ts,
                                    is_pre_migration=False,
                                    is_pre_migration_known=True,
                                    signal_age=age,
                                    min_price=current_price,
                                ))
                                await self.state.add_alerted(addr)
                                logger.info(f"📈 ক্লাইম্বিং সিগন্যাল: {symbol} mcap={format_number(mcap)} score={climbing_score:.2f}")

                                if config.paper_trading:
                                    try:
                                        pos = await self.paper_trader.buy(
                                            addr, symbol, name, current_price,
                                            climbing_score, 0.0, climbing_score, age,
                                        )
                                        if pos:
                                            logger.info(f"📝 Paper buy: {symbol} @ ${current_price:.8f}")
                                    except Exception as e:
                                        logger.debug(f"Paper buy error: {e}")
                        continue

                    if not await self.state.is_alerted(addr) and 0 < age <= 600:
                        # Time-of-day filter
                        current_hour = datetime.now(timezone.utc).hour
                        if current_hour in {2, 10, 22}:
                            continue

                        launch_data = await self.state.get_launch_tracking(addr)
                        if not launch_data:
                            continue

                        pair_data = pair
                        features = extract_launch_features(
                            launch_data, pair_data=pair_data,
                            unique_wallets=len(launch_data.unique_wallets)
                        )

                        vol_24h = float((pair.get("volume") or {}).get("h24", 0) or 0)
                        vol_liq = vol_24h / max(liquidity, 1) if liquidity > 0 else 0
                        buys_5m = int(((pair.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
                        sells_5m = int(((pair.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
                        buy_sell_5m = buys_5m / max(sells_5m, 1)

                        price_change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
                        price_change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)

                        score = 0.0
                        reasons = []

                        if mcap >= 5000 and mcap <= 500000:
                            score += 0.2
                            reasons.append(f"MCap {format_number(mcap)}")
                        if liquidity >= 1000:
                            score += 0.15
                            reasons.append(f"Liq ${int(liquidity)}")
                        if vol_liq >= 0.3:
                            score += 0.15
                            reasons.append(f"Vol/Liq {vol_liq:.1f}")
                        if buy_sell_5m >= 2.0:
                            score += 0.15
                            reasons.append(f"5m B/S {buy_sell_5m:.1f}")
                        if buys_5m >= 10:
                            score += 0.1
                            reasons.append(f"{buys_5m} buys/5m")
                        if price_change_1h > 50:
                            score += 0.2
                            reasons.append(f"1h +{price_change_1h:.0f}%")
                        elif price_change_1h > 20:
                            score += 0.1
                            reasons.append(f"1h +{price_change_1h:.0f}%")
                        if price_change_5m > 10:
                            score += 0.1
                            reasons.append(f"5m +{price_change_5m:.0f}%")

                        social_score = 0.0
                        try:
                            social_score, _ = await self.social.calculate_social_score(addr, symbol)
                            if not isinstance(social_score, (int, float)):
                                social_score = 0.0
                        except Exception:
                            pass
                        if social_score > 0.3:
                            score += 0.1
                            reasons.append(f"Soc {int(social_score*100)}%")

                        if score < 0.55:
                            logger.info(f"[EVAL] {symbol}: score={score:.2f} → SKIP")
                            continue

                        momentum_ok, momentum_reason = await self._check_momentum(addr, launch_data)
                        if not momentum_ok:
                            logger.info(f"🚫 {symbol}: {momentum_reason}")
                            continue

                        confidence_pct = int(score * 100)
                        confidence_bar = "🟢" * max(1, int(confidence_pct/20)) + "⚪" * (5 - max(1, int(confidence_pct/20)))
                        reason_text = ", ".join(reasons[:3])

                        await send_signal(self.telegram_app.bot,
                            f"⚡ আর্লি সিগন্যাল!\n"
                            f"━━━━━━━━━━━━━━━━\n"
                            f"🏷️ {name} (${symbol})\n"
                            f"📍 {addr}\n"
                            f"🎯 কনফিডেন্স: {confidence_bar} {confidence_pct}%\n"
                            f"🧠 {reason_text}\n"
                            f"💵 দাম: {current_price:.8f}\n"
                            f"💰 MCap: {format_number(mcap)}\n"
                            f"💧 লিকুইডিটি: {format_number(liquidity)}\n"
                            f"👥 হোল্ডার: {coin_info.holders}\n"
                            f"🔒 LP লক: {coin_info.lp_locked}%\n"
                            f"⏱️ বয়স: {int(age//60)}m {int(age%60)}s\n"
                            f"━━━━━━━━━━━━━━━━\n"
                            f"🔗 GMGN: {link}\n"
                            f"🔗 DexScreener: {dexscreener_link(addr)}",
                            addr
                        )

                        from bot_state import SignalInfo
                        await self.state.add_signal(addr, SignalInfo(
                            symbol=symbol,
                            price_at_signal=current_price,
                            signal_time=now_ts,
                            launch_time=coin_info.launch_time or now_ts,
                            is_pre_migration=False,
                            is_pre_migration_known=True,
                            signal_age=age,
                            min_price=current_price,
                        ))
                        await self.state.add_alerted(addr)

                        if config.paper_trading:
                            try:
                                launch_vel = getattr(launch_data, 'buy_velocity', 0) if launch_data else 0
                                launch_curve = getattr(launch_data, 'curve_fill_pct', 0) if launch_data else 0
                                pos = await self.paper_trader.buy(
                                    addr, symbol, name, current_price,
                                    score, social_score, score, age,
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

                    coin_info = {
                        "name": pair.get("baseToken", {}).get("name", "Unknown"),
                        "symbol": pair.get("baseToken", {}).get("symbol", "???"),
                    }

                    mcap = float(pair.get("fdv", 0) or 0)
                    if mcap >= PUMP_THRESHOLD:
                        record_launch(addr, coin_info["symbol"], {
                            "launch_time": datetime.now(timezone.utc).timestamp(),
                            "source": "historical_scan",
                        })
                        learned_pump += 1
                    elif mcap < 500 and age and age > 3600:
                        h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
                        if h24 < -30:
                            record_signal_result(addr, coin_info["symbol"], 0.5)
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
        CHECK_INTERVALS = [300, 900, 3600, 21600]
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

                    if current_price > sig_info.ath_price:
                        sig_info.ath_price = current_price

                    next_check = None
                    for interval in CHECK_INTERVALS:
                        if age >= interval and not sig_info.eval_done.get(str(interval), False):
                            next_check = interval
                            break

                    if next_check is None:
                        continue

                    sig_info.eval_done[str(next_check)] = True

                    ath_multiplier = sig_info.ath_price / sig_info.price_at_signal if sig_info.price_at_signal > 0 else 0
                    current_multiplier = current_price / sig_info.price_at_signal if sig_info.price_at_signal > 0 else 0
                    min_price_multiplier = sig_info.min_price / sig_info.price_at_signal if sig_info.price_at_signal > 0 and sig_info.min_price > 0 else current_multiplier

                    if next_check == 21600:
                        emoji = "✅" if ath_multiplier >= 2.0 else ("😐" if ath_multiplier >= 0.8 else "❌")
                        await send_msg(self.telegram_app.bot,
                            f"{emoji} <b>সিগন্যাল ফলাফল (6h)!</b>\n"
                            f"🏷️ ${sig_info.symbol}\n"
                            f"📈 ATH: <b>{ath_multiplier:.2f}x</b>\n"
                            f"📊 বর্তমান: <b>{current_multiplier:.2f}x</b>\n"
                            f"💰 {'🟢 ATH 2x+' if ath_multiplier >= 2 else '🔴 Missed' if ath_multiplier < 0.8 else '➡️ Neutral'}"
                        )
                        record_signal_result(addr, sig_info.symbol, ath_multiplier, current_multiplier, sig_info.signal_age, sig_info.signal_time, min_price_multiplier)
                        await self.state.mark_signal_checked(addr)
                        logger.info(f"[OUTCOME] {sig_info.symbol}: ATH={ath_multiplier:.2f}x current={current_multiplier:.2f}x @ T+6h")
                        try:
                            compute_signal_criteria()
                        except Exception:
                            pass
                        # Auto-learn after new data
                        try:
                            from learner import enhanced_auto_learn
                            enhanced_auto_learn()
                        except Exception:
                            pass
                        # Send updated TP/SL recommendation after each result
                        try:
                            from learner import calculate_optimal_tp_sl, load_data
                            data = load_data()
                            all_results = data.get("model", {}).get("signal_results", [])
                            recent = all_results[-50:]  # last 50 results
                            if len(recent) >= 5:
                                opt = calculate_optimal_tp_sl(recent)
                                await send_msg(self.telegram_app.bot,
                                    f"🔄 <b>TP/SL আপডেট ({sig_info.symbol} এর পর)</b>\n"
                                    f"━━━━━━━━━━━━━━━━\n"
                                    f"⭐ <b>স্ক্রেপারে সেট করো:</b>\n"
                                    f"  <b>TP +{opt['optimal_tp']}%</b> / <b>SL {opt['optimal_sl']}%</b>\n"
                                    f"  → {opt['tp_hits']}/{len(recent)} হিট ({round(opt['tp_hits']/len(recent)*100)}%)\n"
                                    f"  → গড় লাভ: <b>{opt['expected_pnl']:+.1f}%</b>\n"
                                    f"━━━━━━━━━━━━━━━━\n"
                                    f"📊 {len(recent)} সিগন্যাল বিশ্লেষণ"
                                )
                        except Exception:
                            pass
                    else:
                        logger.debug(f"[CHECK] {sig_info.symbol}: T+{next_check//60}m ath={ath_multiplier:.2f}x cur={current_multiplier:.2f}x")

                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"সিগন্যাল চেক এরর: {e}")

    async def signal_confirmation_loop(self):
        """Multi-stage confirmation with volume + momentum + price."""
        CHECK_INTERVAL = 30
        STAGE1_DELAY = 60    # 1 min: initial check
        STAGE2_DELAY = 180   # 3 min: momentum check
        STAGE3_DELAY = 300   # 5 min: final confirmation
        MAX_WAIT = 480       # 8 min: expire

        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                now = datetime.now(timezone.utc).timestamp()
                expired = []

                for addr, pending in list(self.state.pending_signals.items()):
                    elapsed = now - pending.pending_since

                    # Fetch current data
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        if elapsed > MAX_WAIT:
                            expired.append(addr)
                        continue

                    current_price = float(pair.get("priceUsd", 0) or 0)
                    if current_price <= 0:
                        if elapsed > MAX_WAIT:
                            expired.append(addr)
                        continue

                    # Track minimum price after signal
                    if pending.min_price <= 0 or current_price < pending.min_price:
                        pending.min_price = current_price

                    price_ratio = current_price / pending.price_at_match if pending.price_at_match > 0 else 0
                    pending.check_count += 1
                    pending.last_check_price = current_price

                    # Get volume data for volume confirmation
                    volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0) if isinstance(pair.get("volume"), dict) else 0
                    buys_5m = int((pair.get("txns", {}).get("m5", {}) or {}).get("buys", 0) or 0) if isinstance(pair.get("txns"), dict) else 0
                    sells_5m = int((pair.get("txns", {}).get("m5", {}) or {}).get("sells", 0) or 0) if isinstance(pair.get("txns"), dict) else 0
                    buy_pressure_5m = buys_5m / max(buys_5m + sells_5m, 1)

                    # STAGE 1: After 1 min — quick reject dumps, check initial momentum
                    if elapsed >= STAGE1_DELAY and not pending.price_stable:
                        if price_ratio < 0.90:
                            # Dropped >10% → dump
                            logger.info(f"[CONFIRM REJECT] {pending.symbol}: dropped {(1-price_ratio)*100:.0f}% at T+{elapsed:.0f}s")
                            expired.append(addr)
                            continue
                        if price_ratio >= 1.10:
                            # Up 10%+ → strong momentum, check volume
                            if buy_pressure_5m >= 0.6:  # 60%+ buy pressure
                                logger.info(f"[CONFIRMED-FAST] {pending.symbol}: +{(price_ratio-1)*100:.0f}% vol={buy_pressure_5m:.0%} at T+{elapsed:.0f}s")
                                await self._send_confirmed_signal(addr, pending, current_price)
                                expired.append(addr)
                                continue
                        pending.price_stable = True
                        logger.info(f"[STAGE1] {pending.symbol}: stable at {price_ratio:.2f}x, waiting for volume+momentum")
                        continue

                    # STAGE 2: After 3 min — require positive price action + volume
                    if elapsed >= STAGE2_DELAY and not getattr(pending, 'stage2_done', False):
                        if price_ratio < 0.93:
                            logger.info(f"[CONFIRM REJECT] {pending.symbol}: weak at {(price_ratio-1)*100:+.0f}% T+{elapsed:.0f}s")
                            expired.append(addr)
                            continue
                        if price_ratio >= 1.05 and buy_pressure_5m >= 0.55:
                            # Up 5%+ with buy pressure → confirmed
                            logger.info(f"[CONFIRMED-STAGE2] {pending.symbol}: +{(price_ratio-1)*100:.0f}% vol={buy_pressure_5m:.0%} at T+{elapsed:.0f}s")
                            await self._send_confirmed_signal(addr, pending, current_price)
                            expired.append(addr)
                            continue
                        pending.stage2_done = True
                        logger.info(f"[STAGE2] {pending.symbol}: {price_ratio:.2f}x vol={buy_pressure_5m:.0%}, waiting for final")
                        continue

                    # STAGE 3: After 5 min — final confirmation
                    if elapsed >= STAGE3_DELAY:
                        if price_ratio < 0.95:
                            logger.info(f"[CONFIRM REJECT] {pending.symbol}: flat {(price_ratio-1)*100:+.0f}% T+{elapsed:.0f}s")
                            expired.append(addr)
                            continue
                        if price_ratio >= 1.03:
                            # Up 3%+ → confirmed (relaxed after 5 min)
                            logger.info(f"[CONFIRMED] {pending.symbol}: +{(price_ratio-1)*100:.0f}% at T+{elapsed:.0f}s")
                            await self._send_confirmed_signal(addr, pending, current_price)
                            expired.append(addr)
                            continue
                        # Flat after 5 min → no interest, expire
                        logger.info(f"[CONFIRM EXPIRED] {pending.symbol}: flat {price_ratio:.2f}x after {elapsed:.0f}s")
                        expired.append(addr)
                        continue

                    # Expire after MAX_WAIT
                    if elapsed > MAX_WAIT:
                        expired.append(addr)

                for addr in expired:
                    self.state.pending_signals.pop(addr, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"signal_confirmation_loop error: {e}")

    async def _send_confirmed_signal(self, address: str, pending, current_price: float):
        """Send a confirmed signal to Telegram with confidence level."""
        link = gmgn_link(address)
        confidence_pct = int(pending.match_score * 100)

        # Pattern confidence scoring
        if pending.match_score >= 0.80:
            confidence_level = "🟢 STRONG"
            confidence_bar = "🟢🟢🟢🟢🟢"
        elif pending.match_score >= 0.70:
            confidence_level = "🟢 HIGH"
            confidence_bar = "🟢🟢🟢🟢⚪"
        elif pending.match_score >= 0.60:
            confidence_level = "🟡 MEDIUM"
            confidence_bar = "🟢🟢🟢⚪⚪"
        elif pending.match_score >= 0.50:
            confidence_level = "🟡 LOW"
            confidence_bar = "🟢🟢⚪⚪⚪"
        else:
            confidence_level = "🔴 WEAK"
            confidence_bar = "🟢⚪⚪⚪⚪"

        # Fetch current market cap and LP analysis
        current_mcap = 0
        lp_text = ""
        try:
            pair = await self.dex.fetch_pair_data(address)
            if pair:
                current_mcap = float(pair.get("fdv", 0) or 0)
        except Exception:
            pass

        try:
            lp_analysis = await self.birdeye.get_lp_analysis(address) if self.birdeye else None
            if lp_analysis:
                lp_count = lp_analysis.get("lp_providers_count", 0)
                dep_lp = lp_analysis.get("deployer_has_lp", False)
                lp_risk = lp_analysis.get("risk_level", "unknown")
                lp_emoji = "🟢" if lp_risk == "safe" else ("🟡" if lp_risk == "warning" else "🔴")
                lp_text = f"{lp_emoji} LP Providers: {lp_count}"
                if dep_lp:
                    lp_text += " | ⚠️ Deployer has LP"
        except Exception:
            pass

        # Time context
        now_hour = datetime.now(timezone.utc).hour
        if 0 <= now_hour < 8:
            session = "🌏 Asian"
        elif 8 <= now_hour < 16:
            session = "🌍 European"
        else:
            session = "🌎 US"

        await send_signal(self.telegram_app.bot,
            f"⚡ প্রি-মাইগ্রেশন সিগন্যাল!\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🏷️ {pending.name} (${pending.symbol})\n"
            f"📍 {address}\n"
            f"🎯 কনফিডেন্স: {confidence_bar} {confidence_pct}%\n"
            f"📊 Level: {confidence_level}\n"
            f"🧠 {pending.match_reason}\n"
            f"💰 MCap: {format_number(pending.mcap)}\n"
            f"💧 লিকুইডিটি: ${int(pending.liquidity)}\n"
            f"📊 Buy: {pending.buy_count} | Sell: {pending.sell_count}\n"
            f"👥 Wallets: {pending.unique_wallets} | Holders: {pending.holders}\n"
            f"⏱️ বয়স: {int(pending.age_seconds//60)}m {int(pending.age_seconds%60)}s\n"
            f"🕐 Session: {session}\n"
            f"{lp_text + chr(10) if lp_text else ''}"
            f"📈 বর্তমান MCap: {format_number(current_mcap)}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔗 GMGN: {link}\n"
            f"🔗 DexScreener: {dexscreener_link(address)}",
            address
        )

        await self.state.add_alerted(address)

        from bot_state import SignalInfo
        await self.state.add_signal(address, SignalInfo(
            symbol=pending.symbol,
            price_at_signal=pending.price_at_match,
            signal_time=datetime.now(timezone.utc).timestamp(),
            launch_time=0,
            is_pre_migration=True,
            signal_age=pending.age_seconds,
            min_price=pending.min_price if pending.min_price > 0 else pending.price_at_match,
        ))

        logger.info(f"⚡ প্রি-মাইগ্রেশন সিগন্যাল: {pending.symbol} match={pending.match_score:.0%} "
                     f"liq=${int(pending.liquidity)} holders={pending.holders} "
                     f"mcap={format_number(pending.mcap)} confirmed_after={pending.check_count}x checks")

        # Save LP snapshot for monitoring
        try:
            lp_analysis = await self.birdeye.get_lp_analysis(address) if self.birdeye else None
            lp_count = lp_analysis.get("lp_providers_count", 0) if lp_analysis else 0
            dep_has_lp = lp_analysis.get("deployer_has_lp", False) if lp_analysis else False
            await self.state.save_lp_snapshot(
                address, pending.symbol, pending.liquidity,
                lp_count, dep_has_lp, pending.price_at_match,
            )
        except Exception:
            pass

    async def cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(config.cleanup_interval)
                await self.state.cleanup_old_entries()

                try:
                    async with self.state._lock:
                        hp_snapshot = set(self.state.honeypot_addresses)
                        dep_snapshot = set(self.state.blocked_deployers)
                        alerted_snapshot = set(self.state.alerted_coins)
                    save_honeypot_blocklist(hp_snapshot, dep_snapshot, alerted_snapshot)
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
                                        if addr in self.state.signal_tracking:
                                            sig_info = self.state.signal_tracking[addr]
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
        Monitoring: ATH + honeypot detection at T+5/15/60/3600.
        Outcome recording at T+6h for pump/dump classification.
        """
        eval_offsets = [300, 900, 3600, 21600]
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

                    if next_offset == 21600:
                        mcap = float(pair.get("fdv", 0) or 0)
                        result = check_and_record_outcome(addr, mcap)
                        if result:
                            logger.info(f"📊 Outcome {ld.symbol}: {result} (mcap={int(mcap)})")

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
        """Send midnight signal summary with full stats."""
        from datetime import timedelta
        while True:
            try:
                now = datetime.now(timezone.utc)
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if now >= midnight:
                    midnight += timedelta(days=1)
                wait_sec = (midnight - now).total_seconds()
                logger.info(f"📊 Midnight summary in {int(wait_sec//3600)}h {int((wait_sec%3600)//60)}m")
                await asyncio.sleep(wait_sec)

                yesterday = datetime.now(timezone.utc).timestamp() - 86400

                all_signals = self.state.signal_tracking
                recent = {k: v for k, v in all_signals.items() if v.signal_time >= yesterday}

                data = load_data()
                results = data.get("model", {}).get("signal_results", [])
                recent_results = [r for r in results if r.get("timestamp", "") >= datetime.now(timezone.utc).replace(hour=0, minute=0).isoformat()]

                total = len(recent)
                confirmed = sum(1 for v in recent.values() if v.eval_done.get("21600"))

                wins = [r for r in recent_results if r.get("verdict") in ("PUMP", "STRONG_PUMP")]
                losses = [r for r in recent_results if r.get("verdict") == "DUMP"]
                win_rate = len(wins) / max(len(recent_results), 1) * 100

                rows = []
                for addr, sig in sorted(recent.items(), key=lambda x: x[1].signal_time, reverse=True):
                    ath_mult = sig.ath_price / sig.price_at_signal if sig.price_at_signal > 0 else 0
                    if ath_mult >= 5:
                        emoji = "🔥"
                    elif ath_mult >= 2:
                        emoji = "✅"
                    elif ath_mult >= 1:
                        emoji = "😐"
                    else:
                        emoji = "❌"
                    rows.append(f"{emoji} <b>{sig.symbol}</b>: {ath_mult:.1f}x")

                body = "\n".join(rows[:15])
                if len(rows) > 15:
                    body += f"\n... এবং {len(rows)-15}টি আরও"

                insights = data.get("model", {}).get("auto_learn_insights", {})
                learn_text = ""
                if insights:
                    learn_text = (
                        f"━━━━━━━━━━━━━━━━\n"
                        f"🧠 <b>Auto-Learn:</b>\n"
                        f"  Win: {insights.get('avg_win_holders',0)} holders, {insights.get('avg_win_bsr',0)} BSR\n"
                        f"  Loss: {insights.get('avg_loss_holders',0)} holders, {insights.get('avg_loss_bsr',0)} BSR\n"
                    )

                await send_msg(self.telegram_app.bot,
                    f"📊 <b>দৈনিক সামারি</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📡 মোট সিগন্যাল: <b>{total}</b>\n"
                    f"✅ কনফার্মড: <b>{confirmed}</b>\n"
                    f"🟢 জিতেছে: <b>{len(wins)}</b> ({win_rate:.0f}%)\n"
                    f"🔴 হারেছে: <b>{len(losses)}</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"{body}\n"
                    f"{learn_text}"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"🕐 <i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC</i>"
                )
                logger.info(f"📊 Daily summary sent: {total} signals, {win_rate:.0f}% win rate")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"daily_summary_loop error: {e}")

    async def auto_learn_loop(self):
        """Periodic auto-learn: analyze outcomes, adjust heuristic weights, recompute signal criteria."""
        await asyncio.sleep(600)
        while True:
            try:
                insights = auto_learn_update()
                if insights.get("win_rate") and insights.get("total_recent", 0) >= 20:
                    wr = insights["win_rate"]
                    logger.info(f"🧠 Auto-learn: win_rate={wr}% | "
                                f"bsr: {insights.get('avg_win_bsr',0)} vs {insights.get('avg_loss_bsr',0)} | "
                                f"holders: {insights.get('avg_win_holders',0)} vs {insights.get('avg_loss_holders',0)} | "
                                f"liq: {insights.get('avg_win_liq',0)} vs {insights.get('avg_loss_liq',0)} | "
                                f"lp: {insights.get('avg_win_lp_locked',0)} vs {insights.get('avg_loss_lp_locked',0)}")
                criteria = compute_signal_criteria()
                logger.info(f"🎯 Criteria: bsr≥{criteria['min_bsr']} holders≥{criteria['min_holders']} "
                            f"wallets≥{criteria['min_wallets']} liq≥${int(criteria['min_liq'])} "
                            f"liq%≥{criteria['min_liq_pct']} lp≥{criteria['min_lp_locked']}%")
                divergence = learn_divergence_point()
                if divergence.get("optimal_confirm_delay"):
                    delay = divergence["optimal_confirm_delay"]
                    logger.info(f"🔬 Divergence: pump_avg={divergence.get('pump_avg_age',0)}s "
                                f"dump_avg={divergence.get('dump_avg_age',0)}s "
                                f"→ optimal_delay={delay:.0f}s sep={divergence.get('separation_score',0):.3f}")
                data = load_data()
                pump_count = len(data.get("pump_patterns", []))
                dump_count = len(data.get("dump_patterns", []))
                missed = len(data.get("missed_pumps", []))
                if pump_count > 0 or dump_count > 0:
                    logger.info(f"🧠 Patterns: {pump_count} pump + {dump_count} dump + {missed} missed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"auto_learn_loop error: {e}")
            await asyncio.sleep(3600)

    async def _enrich_pump_patterns(self):
        """Fetch DexScreener data for pump tokens and enrich patterns with real data."""
        try:
            data = load_data()
            sr = data.get("model", {}).get("signal_results", [])
            pumps = [r for r in sr if r.get("verdict") in ("PUMP", "STRONG_PUMP")]
            
            if not pumps:
                return
            
            # Build address list
            address_list = []
            seen = set()
            for r in pumps:
                addr = r.get("address", "")
                if addr and addr not in seen:
                    seen.add(addr)
                    address_list.append(addr)
            
            if not address_list:
                return
            
            # Fetch in batches
            dex_results = []
            batch_size = 20
            for i in range(0, len(address_list), batch_size):
                chunk = address_list[i:i+batch_size]
                try:
                    result = await self.dex.fetch_token_data_batch(chunk)
                    if result:
                        dex_results.extend(result)
                    await asyncio.sleep(0.5)  # Rate limit
                except Exception as e:
                    logger.debug(f"Failed to fetch batch: {e}")
            
            if not dex_results:
                return
            
            # Index by address
            dex_by_addr = {}
            for d in dex_results:
                addr = d.get("baseToken", {}).get("address", "")
                if addr not in dex_by_addr:
                    dex_by_addr[addr] = d
            
            # Build enriched patterns
            new_patterns = []
            seen_symbols = set()
            
            for r in pumps:
                addr = r.get("address", "")
                sym = r.get("symbol", "?")
                if sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                
                dex = dex_by_addr.get(addr, {})
                if not dex:
                    continue
                
                # Extract all data
                txns_h1 = dex.get("txns", {}).get("h1", {}) or {}
                buys = int(txns_h1.get("buys", 0) or 0)
                sells = int(txns_h1.get("sells", 0) or 0)
                bsr = buys / max(sells, 1)
                
                liq_raw = dex.get("liquidity", {})
                liq_usd = float(liq_raw.get("usd", 0) or 0) if isinstance(liq_raw, dict) else float(liq_raw or 0)
                
                mcap = float(dex.get("fdv", 0) or 0)
                price = float(dex.get("priceUsd", 0) or 0)
                
                pair_created = dex.get("pairCreatedAt", 0)
                launch_hour = 0
                if pair_created:
                    launch_hour = datetime.fromtimestamp(pair_created / 1000, tz=timezone.utc).hour
                
                liq_pct = round((liq_usd / max(mcap, 1)) * 100, 2)
                
                pair_address = dex.get("pairAddress", "")
                dexscreener_url = f"https://dexscreener.com/solana/{pair_address}" if pair_address else ""
                
                features = {
                    "buy_sell_ratio": round(bsr, 2),
                    "holders": 0,
                    "unique_wallets": buys,
                    "snipers_30s": 0,
                    "insiders_30s": 0,
                    "lp_locked": 0,
                    "liq_pct": liq_pct,
                    "launch_hour": launch_hour,
                    "initial_liq": liq_usd,
                    "liquidity": liq_usd,
                    "mcap": mcap,
                    "volume": float(dex.get("volume", {}).get("h1", 0) or 0),
                    "buy_count": buys,
                    "sell_count": sells,
                    "price": price,
                    "outcome": "pump",
                    "ath_multiplier": r.get("multiplier", 0),
                    "symbol": sym,
                    "address": addr,
                    "source": "signal_results",
                    "learned_at": datetime.now(timezone.utc).isoformat(),
                    "dexscreener_url": dexscreener_url,
                    "pair_created_at": pair_created,
                }
                new_patterns.append(features)
            
            if new_patterns:
                # Merge with existing
                existing = data.get("pump_patterns", [])
                existing_symbols = set(p.get("symbol", "?") for p in existing)
                
                for p in new_patterns:
                    if p["symbol"] not in existing_symbols:
                        existing.append(p)
                        existing_symbols.add(p["symbol"])
                
                data["pump_patterns"] = existing
                save_data(data)
                logger.info(f"🧠 Enriched {len(new_patterns)} pump patterns with DexScreener data")
                
        except Exception as e:
            logger.debug(f"_enrich_pump_patterns error: {e}")

    async def continuous_learn_loop(self):
        """Continuous background learning: enrich patterns, backtest recent data, update criteria every 6 hours."""
        await asyncio.sleep(600)  # Wait 10 min after start
        while True:
            try:
                logger.info("🔄 Continuous learn cycle started...")
                
                # 0. Merge collected pump patterns from pump_collector
                try:
                    from pump_collector import extract_collected_pump_patterns, COLLECTOR_DATA_FILE
                    data = load_data()
                    existing_addrs = {p.get("address", "") for p in data.get("pump_patterns", [])}
                    collected = extract_collected_pump_patterns(COLLECTOR_DATA_FILE)
                    new_count = 0
                    for p in collected:
                        if p["address"] and p["address"] not in existing_addrs:
                            data.setdefault("pump_patterns", []).append(p)
                            existing_addrs.add(p["address"])
                            new_count += 1
                    if new_count > 0:
                        save_data(data)
                        logger.info(f"📥 Merged {new_count} new pump patterns from collector")
                except Exception as e:
                    logger.debug(f"Collector merge error: {e}")
                
                # 1. Enrich pump patterns from recent signal_results with DexScreener data
                await self._enrich_pump_patterns()
                
                # 2. Recompute signal criteria from updated patterns
                criteria = compute_signal_criteria()
                logger.info(f"🎯 Criteria updated: bsr≥{criteria['min_bsr']} holders≥{criteria['min_holders']} "
                            f"wallets≥{criteria['min_wallets']} liq≥${int(criteria['min_liq'])} "
                            f"liq%≥{criteria['min_liq_pct']} lp≥{criteria['min_lp_locked']}%")
                
                # 3. Run quick backtest on recent signals (last 3 days)
                engine = BacktestEngine(
                    self.session, self.dex, self.helius,
                    lambda t: None  # Silent backtest
                )
                result = await engine.run(days=3, max_tokens=50)
                if result:
                    logger.info(f"📊 Backtest: {result.get('total_signals', 0)} signals, "
                                f"win_rate={result.get('win_rate', 0):.1f}%, "
                                f"avg_pnl={result.get('avg_pnl', 0):.1f}%")
                
                # 4. Sync enriched data to GitHub
                await sync_to_github("Continuous learn: enriched patterns, updated criteria")
                
                data = load_data()
                pump_count = len(data.get("pump_patterns", []))
                dump_count = len(data.get("dump_patterns", []))
                logger.info(f"🧠 Patterns: {pump_count} pump + {dump_count} dump")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"continuous_learn_loop error: {e}")
            await asyncio.sleep(21600)  # Every 6 hours

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
        """Silent self-healing: kill duplicate processes, fix common errors, notify only on real fixes."""
        import os, subprocess
        my_pid = os.getpid()
        instance_name = os.environ.get("BOT_INSTANCE", "main")
        lock_file = f"/tmp/meme_bot_{instance_name}.lock"
        last_notify = 0
        notified = set()

        # Write current PID to lock file
        try:
            with open(lock_file, "w") as f:
                f.write(str(my_pid))
        except Exception:
            pass

        while True:
            try:
                await asyncio.sleep(60)

                # --- 1. Kill duplicate processes for THIS instance only ---
                try:
                    if os.path.exists(lock_file):
                        with open(lock_file) as f:
                            old_pid = int(f.read().strip())
                        if old_pid != my_pid:
                            try:
                                os.kill(old_pid, 0)  # check if alive
                                os.kill(old_pid, 9)
                                logger.info(f"🔧 Self-heal: killed stale PID {old_pid} (instance: {instance_name})")
                            except ProcessLookupError:
                                pass
                        # Update lock file with current PID
                        with open(lock_file, "w") as f:
                            f.write(str(my_pid))
                except Exception:
                    pass

                # --- 2. Kill zombie daemon.sh / run_247.sh ---
                for zombie in ["daemon.sh", "run_247.sh", "watchdog.sh"]:
                    try:
                        out = subprocess.check_output(
                            ["pgrep", "-f", zombie],
                            text=True, timeout=5
                        ).strip()
                        if out:
                            for pid in out.split("\n"):
                                if pid.strip():
                                    try:
                                        os.kill(int(pid), 9)
                                        logger.info(f"🔧 Self-heal: killed zombie {zombie} PID {pid}")
                                    except (ProcessLookupError, ValueError):
                                        pass
                    except Exception:
                        pass

                # --- 3. Fix trained_addresses list-of-dicts (once) ---
                try:
                    data_file = os.environ.get("DATA_FILE", "bot_data.json")
                    if os.path.exists(data_file) and "trained_merge" not in notified:
                        import json
                        with open(data_file) as f:
                            data = json.load(f)
                        ta = data.get("trained_addresses", [])
                        if isinstance(ta, list) and ta and isinstance(ta[0], dict):
                            data["trained_addresses"] = [a.get("address", str(a)) for a in ta]
                            with open(data_file, "w") as f:
                                json.dump(data, f, indent=2)
                            now = datetime.now(timezone.utc).timestamp()
                            if now - last_notify > 300:
                                notified.add("trained_merge")
                                last_notify = now
                                await send_msg(self.telegram_app.bot, "🔧 <b>Auto-fix:</b> converted trained_addresses from dicts to strings")
                except Exception:
                    pass

                # --- 5. Detect repeated errors in log → notify once per unique error ---
                log_file = os.environ.get("LOG_FILE", "").strip()
                if not log_file:
                    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log")
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(0, 2)
                            size = f.tell()
                            f.seek(max(0, size - 8192))
                            tail = f.readlines()
                        err_patterns = {}
                        for line in tail:
                            if "ERROR" in line or "Traceback" in line:
                                key = line.strip()[:60]
                                err_patterns[key] = err_patterns.get(key, 0) + 1
                        now = datetime.now(timezone.utc).timestamp()
                        for key, count in err_patterns.items():
                            if count >= 5 and key not in notified and now - last_notify > 600:
                                notified.add(key)
                                last_notify = now
                                await send_msg(self.telegram_app.bot,
                                    f"⚠️ <b>Repeated error ({count}x)</b>\n<code>{key[:100]}</code>"
                                )
                                break
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"health_check_loop error: {e}")

    async def lp_monitoring_loop(self):
        """Monitor LP removal and liquidity decrease for active signals."""
        CHECK_INTERVAL = 300  # 5 min
        LIQ_DROP_WARN = 0.20   # 20% drop = warning
        LIQ_DROP_ALERT = 0.50  # 50% drop = emergency

        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                snapshots = await self.state.get_all_lp_snapshots()

                for addr, snap in list(snapshots.items()):
                    try:
                        pair = await self.dex.fetch_pair_data(addr)
                        if not pair:
                            continue

                        current_liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                        if current_liq <= 0 or snap.liquidity_usd <= 0:
                            continue

                        liq_change = (current_liq - snap.liquidity_usd) / snap.liquidity_usd

                        # LP removal: liquidity dropped significantly
                        if liq_change <= -LIQ_DROP_ALERT:
                            await send_msg(self.telegram_app.bot,
                                f"🔴 <b>LP REMOVAL ALERT!</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🏷️ ${snap.symbol}\n"
                                f"💧 Liquidity: ${snap.liquidity_usd:,.0f} → ${current_liq:,.0f} ({liq_change*100:+.0f}%)\n"
                                f"👥 LP Providers: {snap.lp_providers}\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🔴 <b>বিক্রি করো!</b>"
                            )
                            await self.state.remove_lp_snapshot(addr)
                            logger.warning(f"🔴 LP REMOVAL: {snap.symbol} liq dropped {liq_change*100:.0f}%")

                        elif liq_change <= -LIQ_DROP_WARN:
                            await send_msg(self.telegram_app.bot,
                                f"⚠️ <b>Liquidity Decrease!</b>\n"
                                f"🏷️ ${snap.symbol}\n"
                                f"💧 ${snap.liquidity_usd:,.0f} → ${current_liq:,.0f} ({liq_change*100:+.0f}%)"
                            )
                            logger.warning(f"⚠️ LP WARNING: {snap.symbol} liq dropped {liq_change*100:.0f}%")

                        # Update snapshot with current data
                        if current_liq > 0:
                            await self.state.save_lp_snapshot(
                                addr, snap.symbol, current_liq,
                                snap.lp_providers, snap.deployer_has_lp,
                                snap.price_at_snapshot,
                            )

                    except Exception as e:
                        logger.debug(f"lp_monitoring error for {addr}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"lp_monitoring_loop error: {e}")

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


def _global_exception_handler(loop, context):
    msg = context.get("exception", context["message"])
    if "aclose" in str(msg):
        return
    logger.error(f"💥 UNHANDLED EXCEPTION: {msg}", exc_info=context.get("exception"))

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_global_exception_handler)

    bot = MemeBot()

    def handle_signal(signum, frame):
        try:
            logger.warning(f"📴 Signal {signum} received — ignoring (platform keepalive)")
        except:
            pass

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"💥 MAIN CRASH: {e}", exc_info=True)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
