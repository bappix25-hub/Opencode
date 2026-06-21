import asyncio
from pyrogram import Client

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"

app = Client("maestro_user", api_id=API_ID, api_hash=API_HASH, phone_number=PHONE)

async def main():
    await app.connect()
    print("CONNECTED")
    
    # Check if already authorized
    try:
        me = await app.get_me()
        print(f"ALREADY_LOGGED_IN:{me.first_name}")
        await app.disconnect()
        return
    except Exception as e:
        print(f"NOT_AUTHORIZED:{e}")
    
    # Send code request
    try:
        from pyrogram.raw.functions.auth import SendCode
        from pyrogram.raw.types import CodeSettings
        result = await app.invoke(SendCode(
            phone_number=PHONE,
            api_id=API_ID,
            api_hash=API_HASH,
            settings=CodeSettings(
                allow_flashcall=False,
                current_number=True,
                allow_sms=True
            )
        ))
        print(f"CODE_SENT:{result.type}")
    except Exception as e:
        print(f"CODE_ERROR:{e}")
    
    await app.disconnect()

asyncio.run(main())
