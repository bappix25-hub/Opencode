import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import aiohttp
from telegram import Bot
from telegram.ext import Application

from config import config
from dex_client import DexScreenerClient
from birdeye_client import BirdeyeClient
from telegram_bot import TelegramHandlers, register_handlers
from gmgn_snapshot_tracker import GmgnSnapshotTracker
from github_sync import sync_to_github, restore_from_github
from utils import setup_logging

logger = setup_logging("meme_bot")

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chat_id")

async def send_msg(bot: Bot, text: str) -> None:
    try:
        chat_id = config.chat_id
        if not chat_id or chat_id == "0":
            if os.path.exists(CHAT_ID_FILE):
                with open(CHAT_ID_FILE) as f:
                    chat_id = f.read().strip()
        if not chat_id or chat_id == "0":
            logger.warning("No chat_id configured")
            return
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Send error: {e}")

_bot_instance = None

def _get_bot():
    return _bot_instance


class MemeBot:
    def __init__(self):
        self.session: aiohttp.ClientSession = None
        self.dex: DexScreenerClient = None
        self.birdeye: BirdeyeClient = None
        self.tracker: GmgnSnapshotTracker = None
        self.telegram_app: Application = None
        self.handlers: TelegramHandlers = None
        self._shutdown_event = asyncio.Event()
        self._tasks = []

    async def start(self):
        self.session = aiohttp.ClientSession()
        self.dex = DexScreenerClient(self.session)
        self.birdeye = BirdeyeClient(self.session, config.birdeye_api_key)
        self.tracker = GmgnSnapshotTracker(self.dex, self.birdeye)

        self.telegram_app = Application.builder().token(config.bot_token).build()
        self.handlers = TelegramHandlers(None, self.dex, self.session, None)
        register_handlers(self.telegram_app, self.handlers)

        await restore_from_github()

        logger.info("🚀 বট চালু হচ্ছে (GMGN-only mode)...")

        self._tasks = [
            asyncio.create_task(self._gmgn_scan_loop(), name="gmgn_scan"),
            asyncio.create_task(self._snapshot_loop(), name="snapshot"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
        ]

        if config.enable_github_sync:
            self._tasks.append(asyncio.create_task(self._github_sync_loop(), name="github_sync"))

        try:
            self.telegram_app.add_error_handler(self._telegram_error_handler)
            await self.telegram_app.initialize()
            await self.telegram_app.start()

            for attempt in range(10):
                try:
                    await self.telegram_app.updater.start_polling(
                        allowed_updates=["message", "callback_query"],
                    )
                    break
                except Exception as e:
                    if "Conflict" in str(e) and attempt < 9:
                        logger.warning(f"Conflict (attempt {attempt+1}/10), retry 5s...")
                        await asyncio.sleep(5)
                    else:
                        raise

            await send_msg(self.telegram_app.bot,
                "🤖 <b>বট চালু!</b>\n"
                "📡 শুধুমাত্র GMGN Featured V2\n"
                "⏱️ 3 মিনিট পর স্ন্যাপশট শুরু\n"
                "🤖 ৭টি বট + রেটলিমিট সুরক্ষা"
            )
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Start error: {e}")
        finally:
            await self.shutdown()

    def _telegram_error_handler(self, update, context):
        error = context if isinstance(context, Exception) else getattr(context, 'error', context)
        logger.error(f"Telegram error: {error}")

    async def shutdown(self):
        if self._shutdown_event.is_set():
            return
        logger.info("Shutting down...")
        self._shutdown_event.set()

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass

        if self.telegram_app:
            try:
                await self.telegram_app.updater.stop()
            except Exception:
                pass
            try:
                await self.telegram_app.stop()
            except Exception:
                pass
            try:
                await self.telegram_app.shutdown()
            except Exception:
                pass

        try:
            import maestro_client as mc
            await mc.close()
        except Exception:
            pass

        logger.info("✅ Shutdown complete")

    # ===== GMGN SCAN LOOP =====
    async def _gmgn_scan_loop(self):
        """Scan GMGN Featured V2 channel for new tokens. Every 20s."""
        while True:
            try:
                import telegram_collector as tc

                tg_client = None
                try:
                    import maestro_client as mc
                    tg_client = await mc.get_client()
                except Exception:
                    pass

                if tg_client and tg_client.is_connected():
                    new_tokens = await tc.scan_channels(tg_client, self.dex)
                    if new_tokens:
                        for token in new_tokens:
                            ca = token.get("ca", "")
                            symbol = token.get("symbol", "?")
                            if not ca or not symbol:
                                continue
                            liq = token.get("liq_usd", 0)
                            mcp = token.get("mcp", 0)
                            # Skip if no liquidity
                            if liq < 500:
                                continue
                            # Create session directly — no pending, no wait
                            if ca not in self.tracker.sessions:
                                from gmgn_snapshot_tracker import SnapshotSession
                                session = SnapshotSession(
                                    ca=ca, symbol=symbol,
                                    launch_ts=token.get("first_seen", time.time()),
                                    initial_price=0, initial_mcap=mcp, initial_liq=liq,
                                    signal_type=token.get("signal_type", ""),
                                    source="bot",
                                )
                                session.last_dex_data = None
                                self.tracker.sessions[ca] = session
                                logger.info(f"🤖 New: {symbol} ({ca[:8]}...) liq=${liq:.0f} mcp=${mcp:.0f}")
                            # IMMEDIATE snapshot via bot
                            try:
                                await asyncio.wait_for(self.tracker.take_snapshot(ca), timeout=15)
                            except Exception as e:
                                logger.info(f"Snap error {symbol}: {e}")
                else:
                    logger.debug("TG client not available for GMGN scan")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GMGN scan error: {e}")
            await asyncio.sleep(20)

    # ===== SNAPSHOT LOOP =====
    async def _snapshot_loop(self):
        """Take snapshots every ~60s."""
        while True:
            try:
                await self.tracker.scan_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Snapshot error: {e}")
            await asyncio.sleep(60)

    # ===== GITHUB SYNC (every 1 hour) =====
    async def _github_sync_loop(self):
        """Push data to GitHub every 60 minutes."""
        while True:
            try:
                await asyncio.sleep(3600)
                if config.enable_github_sync:
                    await sync_to_github("🔄 Hourly GMGN snapshot data sync")
                    logger.info("✅ GitHub sync complete")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GitHub sync error: {e}")

    # ===== HEARTBEAT (keep-alive) =====
    async def _heartbeat_loop(self):
        """Log status every 15 min."""
        while True:
            try:
                await asyncio.sleep(900)
                stats = self.tracker.get_session_stats()
                active = len(self.tracker.sessions)
                pending = len(self.tracker._pending_tokens)
                completed = len(self.tracker.completed)
                try:
                    from multi_bot_client import get_bot_stats
                    bs = get_bot_stats()
                    working = sum(1 for v in bs.values() if v["total"] > 0 and not v["skipped"])
                    bot_line = f"| বট {working}/7 সক্রিয়"
                except Exception:
                    bot_line = ""

                msg = (f"💚 <b>GMGN Tracker চলছে</b>\n"
                       f"━━━━━━━━━━━━━━━━\n"
                       f"📡 স্ন্যাপশট: <b>{active}</b> active\n"
                       f"⏳ অপেক্ষমান: <b>{pending}</b> (3মি গেট)\n"
                       f"✅ সম্পন্ন: <b>{completed}</b>\n"
                       f"{bot_line}")
                await send_msg(self.telegram_app.bot, msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")


async def main():
    bot = MemeBot()
    global _bot_instance
    _bot_instance = bot
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
