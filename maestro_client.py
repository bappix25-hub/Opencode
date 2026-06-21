import asyncio
import logging
import os
from telethon import TelegramClient, errors

logger = logging.getLogger("maestro_client")

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
MAESTRO_ID = 5486942816
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

_client = None
_lock = asyncio.Lock()

async def get_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await _client.connect()
        if not await _client.is_user_authorized():
            logger.error("Maestro client not authorized - session expired")
            return None
        await _client.get_entity(MAESTRO_ID)
    return _client

async def buy(address: str, sol_amount: str = "") -> bool:
    try:
        client = await get_client()
        if not client:
            return False
        
        cmd = f"/buy {address}"
        if sol_amount:
            cmd += f" {sol_amount}"
        
        async with _lock:
            await client.send_message(MAESTRO_ID, cmd)
        
        logger.info(f"Maestro buy sent: {address[:12]}...")
        return True
        
    except errors.FloodWaitError as e:
        logger.warning(f"Maestro flood wait: {e.seconds}s")
        return False
    except Exception as e:
        logger.debug(f"Maestro buy error: {e}")
        return False

async def close():
    global _client
    if _client:
        await _client.disconnect()
        _client = None
