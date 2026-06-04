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
    score_coin, score_launch, record_signal, update_signal_result,
    get_stats, get_daily_report, learn_pump, learn_dump,
    extract_launch_pattern, learn_pump_with_launch, verify_pump, get_launch_age,
    get_adaptive_threshold, is_duplicate
)
from github_sync import sync_to_github, restore_from_github
from utils import format_number, gmgn_link, setup_logging

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
        self.pumpportal: PumpPortalWS = None
        self.telegram_app: Application = None
        self.handlers: TelegramHandlers = None
        self._shutdown_event = asyncio.Event()
        self._tasks: list = []

    async def start(self):
        self.session = aiohttp.ClientSession()
        self.dex = DexScreenerClient(self.session)
        self.rugcheck = RugcheckClient(self.session)
        self.helius = HeliusClient(self.session)

        self.pumpportal = PumpPortalWS(
            on_new_token=self._on_new_token,
            on_migration=self._on_migration,
            on_trade=self._on_trade
        )

        self.telegram_app = Application.builder().token(config.bot_token).build()
        self.handlers = TelegramHandlers(self.state, self.dex, self.session)
        register_handlers(self.telegram_app, self.handlers)

        await restore_from_github()
        logger.info("🚀 বট চালু হচ্ছে...")

        self._tasks = [
            asyncio.create_task(self.pumpportal.connect(), name="pumpportal"),
            asyncio.create_task(self.realtime_scan_loop(), name="realtime"),
            asyncio.create_task(self.history_scan_loop(), name="history"),
            asyncio.create_task(self.cleanup_loop(), name="cleanup"),
            asyncio.create_task(self.check_signal_results_loop(), name="signal_check"),
        ]

        if config.enable_github_sync:
            self._tasks.append(asyncio.create_task(self.github_sync_loop(), name="github_sync"))

        await send_msg(self.telegram_app.bot, "🤖 <b>বট v2 চালু!</b>\n✅ সব সিস্টেম রেডি")

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
        if await self.state.get_tracked_coin(address):
            return
        symbol = data.get("symbol", "???")

        await asyncio.sleep(3)
        rug = await self.rugcheck.check_token(address, symbol)
        if rug and rug.is_risky:
            await self.state.add_blacklisted(address)
            logger.info(f"🚫 ব্ল্যাকলিস্ট: {symbol} | {rug.risks[:2]}")
            return

        holders = await self.helius.get_holder_count(address)
        if holders is not None and holders < 3:
            logger.info(f"⚠️ হোল্ডার কম: {symbol} ({holders})")
            return

        from bot_state import LaunchData
        price = float(data.get("initialBuy", 0) or 0)
        now_ts = datetime.now(timezone.utc).timestamp()
        deployer = data.get("traderPublicKey", "") or data.get("deployer", "")

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
        launch_dict = {
            "buy_count": launch_data.buy_count,
            "sell_count": launch_data.sell_count,
            "unique_wallets": len(launch_data.unique_wallets),
            "volume": launch_data.volume,
            "buy_sell_ratio": buy_sell_ratio
        }
        ai_score, reason = score_launch(launch_dict)
        threshold = get_adaptive_threshold()

        if ai_score >= threshold:
            symbol = launch_data.symbol
            name = launch_data.name
            confidence_pct = int(ai_score * 100)
            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
            link = gmgn_link(address)
            await send_msg(self.telegram_app.bot,
                f"⚡ <b>প্রি-মাইগ্রেশন সিগন্যাল!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🏷️ <b>{name}</b> (${symbol})\n"
                f"🎯 কনফিডেন্স: {confidence_bar} <b>{confidence_pct}%</b>\n"
                f"🧠 <i>{reason}</i>\n"
                f"📊 Buy: <b>{launch_data.buy_count}</b> | Sell: <b>{launch_data.sell_count}</b>\n"
                f"👥 Unique wallets: <b>{len(launch_data.unique_wallets)}</b>\n"
                f"⏱️ বয়স: <b>{int(age)}s</b> (launch time থেকে)\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>মাইগ্রেশনের আগে! DYOR করুন!</i>\n"
                f"🔗 <a href='{link}'>GMGN</a>"
            )
            await self.state.add_alerted(address)
            launch_data.pre_signal_sent = True
            logger.info(f"⚡ প্রি-মাইগ্রেশন সিগন্যাল: {symbol} স্কোর: {ai_score}")

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
            logger.info(f"✅ মাইগ্রেশন ট্র্যাক: {symbol}")

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

                for addr, coin_info in list((await self._get_tracked_dict()).items()):
                    if await self.state.is_blacklisted(addr):
                        continue
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
                        if ai_score >= threshold:
                            holders = coin_info.holders
                            lp = coin_info.lp_locked
                            confidence_pct = int(ai_score * 100)
                            confidence_bar = "🟢" * int(confidence_pct/20) + "⚪" * (5 - int(confidence_pct/20))
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
                                f"⏱️ বয়স: <b>{int(age)}s</b> (launch time থেকে)\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"🔗 <a href='{link}'>GMGN</a>"
                            )
                            record_signal(addr, symbol, ai_score, current_price, mcap)
                            from bot_state import SignalInfo
                            await self.state.add_signal(addr, SignalInfo(
                                symbol=symbol,
                                price_at_signal=current_price,
                                signal_time=now_ts
                            ))
                            await self.state.add_alerted(addr)

                sync_counter += 1
                if sync_counter >= config.github_sync_interval // max(config.scan_interval, 1):
                    if config.enable_github_sync:
                        await sync_to_github()
                    sync_counter = 0

                logger.info(f"ট্র্যাক: {stats['tracked_coins']} | লঞ্চ: {stats['launch_tracking']} | পাম্প: {stats['pump_coins']}")
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
                await asyncio.sleep(300)
                unchecked = await self.state.get_unchecked_signals()
                now = datetime.now(timezone.utc).timestamp()
                for addr, sig_info in list(unchecked.items()):
                    age = now - sig_info.signal_time
                    if age < 1800:
                        continue
                    pair = await self.dex.fetch_pair_data(addr)
                    if not pair:
                        continue
                    current_price = float(pair.get("priceUsd", 0) or 0)
                    if current_price <= 0:
                        continue
                    update_signal_result(addr, current_price)
                    multiplier = current_price / sig_info.price_at_signal if sig_info.price_at_signal > 0 else 0
                    emoji = "✅" if multiplier >= 2.0 else "❌"
                    await send_msg(self.telegram_app.bot,
                        f"{emoji} <b>সিগন্যাল ফলাফল!</b>\n"
                        f"🏷️ ${sig_info.symbol}\n"
                        f"📈 ফলাফল: <b>{multiplier:.2f}x</b>"
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
                logger.info("🧹 ক্লিনআপ সম্পন্ন")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ক্লিনআপ এরর: {e}")

    async def github_sync_loop(self):
        while True:
            try:
                await asyncio.sleep(config.github_sync_interval)
                await sync_to_github()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GitHub sync loop error: {e}")

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
