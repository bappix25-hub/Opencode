import asyncio
import logging
import os
import glob
import time
from telethon import TelegramClient, errors

logger = logging.getLogger("meme_bot.maestro_client")

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
MAESTRO_ID = 5486942816
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

_client = None
_lock = asyncio.Lock()
_last_attempt = 0  # timestamp of last connection attempt
_COOLDOWN = 120    # seconds between reconnect attempts


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
    global _client, _last_attempt

    # Fast path: client already connected
    if _client is not None:
        if _client.is_connected():
            return _client
        # Client disconnected — don't reconnect immediately, cooldown
        now = time.time()
        if now - _last_attempt < _COOLDOWN:
            logger.debug("Maestro client disconnected, waiting cooldown")
            return None
        logger.warning("Maestro client disconnected, reconnecting...")

    # Rate limit reconnection attempts
    now = time.time()
    if now - _last_attempt < _COOLDOWN:
        return None
    _last_attempt = now

    # Try up to 2 times
    for attempt in range(2):
        async with _lock:
            if _client is not None and _client.is_connected():
                return _client
            _cleanup_session_locks()
            try:
                new_client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
                await new_client.connect()
            except Exception as e:
                logger.error(f"Telegram connect attempt {attempt+1} failed: {e}")
                _cleanup_session_locks()
                await asyncio.sleep(2)
                continue

            if not await new_client.is_user_authorized():
                logger.error("Maestro client not authorized - session expired")
                await new_client.disconnect()
                return None

            # Pre-resolve entities (non-blocking, errors ignored)
            for bot_id in [MAESTRO_ID, 8436907499, 7178305557, 6126376117,
                           8308748868, 6556421217, 6832064371, 6113783210,
                           7060758339, 7294318663]:
                try:
                    await new_client.get_entity(bot_id)
                except Exception:
                    try:
                        async for dialog in new_client.iter_dialogs(limit=50):
                            if dialog.id == bot_id:
                                break
                    except Exception:
                        pass

            _client = new_client
            logger.info("✅ Maestro client connected and authorized")
            return _client

    # All attempts failed
    if _client:
        try:
            await _client.disconnect()
        except Exception:
            pass
        _client = None
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