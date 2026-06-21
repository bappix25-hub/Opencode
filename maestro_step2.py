"""Step 2: Sign in with OTP code and send hi to Maestro."""
import asyncio
import sys
from telethon import TelegramClient

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
MAESTRO_ID = 5486942816

code = sys.argv[1]

client = TelegramClient("maestro_session", API_ID, API_HASH)

async def main():
    await client.connect()
    
    if not await client.is_user_authorized():
        await client.sign_in(PHONE, code)
    
    me = await client.get_me()
    print(f"LOGGED_IN: {me.first_name} ({me.phone_number})")
    
    await client.send_message(MAESTRO_ID, "hi")
    print("SENT: hi to Maestro!")
    
    await client.disconnect()

asyncio.run(main())
