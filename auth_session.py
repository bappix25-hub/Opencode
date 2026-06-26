#!/usr/bin/env python3
"""
Authorize Maestro session — one command, asks for code interactively.
Usage: python3 auth_session.py
"""
import asyncio
from telethon import TelegramClient

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
SESSION = "/root/Opencode/maestro_session"
PHONE = "8801733446456"

async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already authorized! Logged in as: {me.first_name}")
        await client.disconnect()
        return

    await client.send_code_request(PHONE)
    print(f"Code sent to {PHONE} — check Telegram")

    code = input("Enter code here: ").strip()
    if not code:
        print("No code entered.")
        await client.disconnect()
        return

    try:
        await client.sign_in(PHONE, code)
        print("AUTHORIZED!")
    except Exception as e:
        print(f"Error: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    if me:
        print(f"Logged in as: {me.first_name} (ID: {me.id})")
    await client.disconnect()

asyncio.run(main())
