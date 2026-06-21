import asyncio
import os
import sys
import time
from pyrogram import Client
from pyrogram.raw import functions, types

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
CODE_FILE = "/root/Opencode/otp_code.txt"

app = Client("maestro_user", api_id=API_ID, api_hash=API_HASH, phone_number=PHONE)

async def main():
    try:
        await app.connect()
        me = await app.get_me()
        print(f"LOGGED_IN:{me.first_name}")
    except Exception as e:
        print(f"NEED_CODE:{e}")
        try:
            result = await app.invoke(functions.auth.SendCode(
                phone_number=PHONE,
                api_id=API_ID,
                api_hash=API_HASH,
                settings=types.CodeSettings(
                    allow_flashcall=False,
                    current_number=True,
                    allow_sms=True
                )
            ))
            print(f"CODE_SENT:{result.type}")
        except Exception as e2:
            print(f"CODE_SEND_FAILED:{e2}")
            await app.disconnect()
            return

    start = time.time()
    while time.time() - start < 180:
        if os.path.exists(CODE_FILE):
            with open(CODE_FILE) as f:
                code = f.read().strip()
            if code:
                os.remove(CODE_FILE)
                try:
                    await app.sign_in(PHONE, code)
                    me = await app.get_me()
                    print(f"LOGGED_IN:{me.first_name}")
                    
                    msg = await app.send_message(5486942816, "hi")
                    print(f"SENT:{msg.id}")
                    await app.disconnect()
                    return
                except Exception as e:
                    print(f"SIGN_IN_FAILED:{e}")
                    await app.disconnect()
                    return
        await asyncio.sleep(0.5)
    
    print("TIMEOUT")
    await app.disconnect()

asyncio.run(main())
