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
from gmgn_client import GmgnClient
from pump_portal_client import PumpPortalClient
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
        self.gmgn_client: GmgnClient = None
        self.tracker: GmgnSnapshotTracker = None
        self.telegram_app: Application = None
        self.handlers: TelegramHandlers = None
        self._shutdown_event = asyncio.Event()
        self._tasks = []

    async def start(self):
        self.session = aiohttp.ClientSession()

        self.pump_portal = PumpPortalClient()
        await self.pump_portal.start()

        self.gmgn_client = GmgnClient(pump_portal_client=self.pump_portal)
        await self.gmgn_client.start()

        self.tracker = GmgnSnapshotTracker(self.gmgn_client)

        self.telegram_app = Application.builder().token(config.bot_token).build()
        self.handlers = TelegramHandlers(None, None, self.session, None)
        register_handlers(self.telegram_app, self.handlers)

        await restore_from_github()

        logger.info(" bot chalucchi (DexScreener mode)...")

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
                        if attempt < 9:
                            logger.warning(f"Poll error (attempt {attempt+1}/10): {e}, sleep 10s...")
                            await asyncio.sleep(10)
                        else:
                            raise

            await send_msg(self.telegram_app.bot,
                " <b>Bot chaluchi!</b>\n"
                " DexScreener tracking\n"
                " Free API (no key)\n"
                " 24/7 cholbe"
            )
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Start error: {e}")
        finally:
            await self.shutdown()

    async def _telegram_error_handler(self, update, context):
        error = context if isinstance(context, Exception) else getattr(context, 'error', context)
        err_str = str(error)
        if "Connection" in err_str or "NetworkError" in err_str or "104" in err_str or "103" in err_str:
            logger.warning(f"Network error in TG handler (ignored): {err_str[:100]}")
        else:
            logger.error(f"Telegram handler error: {err_str[:200]}")

    async def shutdown(self):
        if self._shutdown_event.is_set():
            return
        logger.info("Shutting down...")
        self._shutdown_event.set()

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self.pump_portal:
            await self.pump_portal.stop()
        if self.gmgn_client:
            await self.gmgn_client.close()
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

        logger.info(" Shutdown complete")

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
                    new_tokens = await tc.scan_channels(tg_client)
                    if new_tokens:
                        for token in new_tokens:
                            ca = token.get("ca", "")
                            symbol = token.get("symbol", "?")
                            if not ca or not symbol:
                                continue
                            liq = token.get("liq_usd", 0)
                            mcp = token.get("mcp", 0)
                            if liq < 500:
                                continue
                            if ca not in self.tracker.sessions:
                                from gmgn_snapshot_tracker import SnapshotSession
                                session = SnapshotSession(
                                    ca=ca, symbol=symbol,
                                    launch_ts=token.get("first_seen", time.time()),
                                    initial_price=0, initial_mcap=mcp, initial_liq=liq,
                                    signal_type=token.get("signal_type", ""),
                                    source="gmgn",
                                )
                                self.tracker.sessions[ca] = session
                                logger.info(f" New: {symbol} ({ca[:8]}...) liq=${liq:.0f} mcp=${mcp:.0f}")
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
        """Take snapshots every ~60s, then check for candle patterns."""
        while True:
            try:
                await self.tracker.scan_loop()

                signals = self.tracker.check_patterns(180)
                for sig in signals:
                    msg = (
                        f" SIGNAL: <b>{sig['symbol']}</b>\n"
                        f"====================\n"
                        f" Price: <b>${sig['price']:.6f}</b>\n"
                        f" Candle: <b>+{sig['change_pct']}%</b>\n"
                        f" MCap: ${sig['mcap']:.0f}\n"
                        f" Age: {sig['age_hours']}h\n"
                        f" CA: <code>{sig['ca'][:12]}...</code>"
                    )
                    await send_msg(self.telegram_app.bot, msg)
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
                    await sync_to_github(" Hourly GMGN snapshot data sync")
                    logger.info(" GitHub sync complete")
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
                msg = (f" <b>Tracker cholche</b>\n"
                       f"====================\n"
                       f" Snapshot: <b>{active}</b> active\n"
                       f" Opekkhaman: <b>{pending}</b>\n"
                       f" Sampanna: <b>{completed}</b>\n"
                       f" DexScreener")
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
