import asyncio
import logging
import os
import glob
from telethon import TelegramClient, errors

logger = logging.getLogger("meme_bot.maestro_client")

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
MAESTRO_ID = 5486942816
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

_client = None
_lock = asyncio.Lock()


def _cleanup_session_locks():
    """Remove SQLite lock files that cause 'database is locked' errors."""
    for pattern in [f"{SESSION_FILE}*", f"{SESSION_FILE}-*"]:
        for lock_file in glob.glob(pattern):
            if lock_file.endswith(("-journal", "-shm", "-wal", ".lock")):
                try:
                    os.remove(lock_file)
                    logger.info(f"Removed stale lock file: {lock_file}")
                except Exception:
                    pass


async def get_client() -> TelegramClient:
    global _client
    if _client is not None:
        if _client.is_connected():
            return _client
        logger.warning("Maestro client disconnected, reconnecting...")
        try:
            await _client.connect()
            return _client
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            _client = None
    
    # Try up to 3 times with cleanup between attempts
    for attempt in range(3):
        async with _lock:
            if _client is not None:
                return _client
            _cleanup_session_locks()
            try:
                _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
                await _client.connect()
            except Exception as e:
                logger.error(f"Telegram connect attempt {attempt+1} failed: {e}")
                _client = None
                _cleanup_session_locks()
                await asyncio.sleep(1)
                continue
            
            if not await _client.is_user_authorized():
                logger.error("Maestro client not authorized - session expired")
                _client = None
                return None
            
            # Pre-resolve entities
            try:
                await _client.get_entity(MAESTRO_ID)
            except Exception:
                pass
            
            BOT_IDS = [8436907499, 7178305557, 6126376117]
            for bot_id in BOT_IDS:
                try:
                    await _client.get_entity(bot_id)
                except Exception:
                    try:
                        async for dialog in _client.iter_dialogs(limit=100):
                            if dialog.id in BOT_IDS:
                                logger.info(f"Resolved bot entity: {dialog.name} ({dialog.id})")
                    except Exception:
                        pass
                    break
            
            logger.info("✅ Maestro client connected and authorized")
            return _client
    
    return None


async def ensure_connected():
    """Pre-connect at bot startup. Call this once."""
    client = await get_client()
    if client:
        logger.info("🤖 Maestro client ready")
    else:
        logger.warning("⚠️ Maestro client failed to connect")


async def buy(address: str, sol_amount: str = "") -> bool:
    for attempt in range(2):
        try:
            client = await get_client()
            if not client:
                return False
            
            async with _lock:
                await client.send_message(MAESTRO_ID, address)
            
            logger.info(f"🤖 Maestro buy sent: {address[:12]}...")
            return True
            
        except errors.FloodWaitError as e:
            logger.warning(f"Maestro flood wait: {e.seconds}s")
            return False
        except Exception as e:
            logger.warning(f"Maestro buy attempt {attempt+1} error: {e}")
            # Reset client on error and retry
            global _client
            _client = None
            _cleanup_session_locks()
            await asyncio.sleep(1)
    
    return False


async def close():
    global _client
    if _client:
        try:
            await _client.disconnect()
        except Exception:
            pass
        _client = None
        _cleanup_session_locks()