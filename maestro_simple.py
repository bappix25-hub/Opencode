"""Simple direct login script."""
import asyncio
from telethon import TelegramClient

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
MAESTRO_ID = 5486942816

client = TelegramClient("maestro_session", API_ID, API_HASH)

async def main():
    await client.connect()
    print("CONNECTED")
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_LOGGED_IN:{me.first_name}")
        await client.send_message(MAESTRO_ID, "hi")
        print("SENT:hi to Maestro!")
        await client.disconnect()
        return
    
    sent = await client.send_code_request(PHONE)
    print("ENTER_CODE:paste your OTP here")
    
    code = input()
    
    try:
        await client.sign_in(PHONE, code, phone_code_hash=sent.phone_code_hash)
        me = await client.get_me()
        print(f"LOGGED_IN:{me.first_name}")
        
        await client.send_message(MAESTRO_ID, "hi")
        print("SENT:hi to Maestro!")
    except Exception as e:
        print(f"ERROR:{e}")
    
    await client.disconnect()

asyncio.run(main())
