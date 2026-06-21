"""Combined: Request OTP, wait for code file, sign in, send hi."""
import asyncio
import os
import sys
import time
from telethon import TelegramClient

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
MAESTRO_ID = 5486942816
CODE_FILE = "/root/Opencode/otp_code.txt"

client = TelegramClient("maestro_session", API_ID, API_HASH)

def log(msg):
    print(msg, flush=True)

async def main():
    await client.connect()
    log("CONNECTED")
    
    if await client.is_user_authorized():
        me = await client.get_me()
        log(f"LOGGED_IN:{me.first_name}")
        await client.send_message(MAESTRO_ID, "hi")
        log("SENT:hi to Maestro!")
        await client.disconnect()
        return
    
    sent = await client.send_code_request(PHONE)
    log(f"CODE_SENT")
    
    if os.path.exists(CODE_FILE):
        os.remove(CODE_FILE)
    
    log("WAITING_FOR_CODE")
    
    start = time.time()
    while time.time() - start < 180:
        if os.path.exists(CODE_FILE):
            with open(CODE_FILE) as f:
                code = f.read().strip()
            if code:
                os.remove(CODE_FILE)
                log(f"USING_CODE:{code}")
                try:
                    await client.sign_in(PHONE, code, phone_code_hash=sent.phone_code_hash)
                    me = await client.get_me()
                    log(f"LOGGED_IN:{me.first_name}")
                    
                    await client.send_message(MAESTRO_ID, "hi")
                    log("SENT:hi to Maestro!")
                except Exception as e:
                    log(f"SIGN_IN_ERROR:{e}")
                await client.disconnect()
                return
        await asyncio.sleep(0.5)
    
    log("TIMEOUT")
    await client.disconnect()

asyncio.run(main())
