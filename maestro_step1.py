"""Step 1: Request OTP code and save phone_code_hash."""
import asyncio
import json
from telethon import TelegramClient

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"

client = TelegramClient("maestro_session", API_ID, API_HASH)

async def main():
    await client.connect()
    print("CONNECTED")
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_LOGGED_IN:{me.first_name}")
        return
    
    sent = await client.send_code_request(PHONE)
    print(f"CODE_SENT")
    
    with open("/root/Opencode/maestro_hash.txt", "w") as f:
        f.write(sent.phone_code_hash)
    
    await client.disconnect()

asyncio.run(main())
