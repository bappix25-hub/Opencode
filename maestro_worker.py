import asyncio
from pyrogram import Client

app = Client("maestro_user", api_id=26413354, api_hash="d0b3f351eea6bdd0623c75555430552c", phone_number="+8801733446456")

async def main():
    async with app:
        me = await app.get_me()
        print(f"Logged in: {me.first_name} ({me.phone_number})")
        msg = await app.send_message(5486942816, "hi")
        print(f"✅ Sent! ID: {msg.id}")

asyncio.run(main())
