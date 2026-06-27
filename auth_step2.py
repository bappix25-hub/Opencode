#!/usr/bin/env python3
"""Auth Step 2: Complete sign-in with OTP code + phone_code_hash from state."""
import asyncio
import json
import os
import sys
from telethon import TelegramClient, errors

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")
STATE_FILE = SESSION_FILE + "_auth_state.json"

async def main():
    if not os.path.exists(STATE_FILE):
        print("❌ No auth state found. Run auth_step1.py first.")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]
    code = sys.argv[1] if len(sys.argv) > 1 else ""
    if not code:
        print("❌ Usage: python3 auth_step2.py <OTP_CODE>")
        return

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        print("✅ Authorized successfully!")
    except errors.SessionPasswordNeededError:
        print("🔐 2FA password required")
        pwd = input("Password: ").strip()
        await client.sign_in(password=pwd)
        print("✅ Authorized with 2FA!")
    except errors.PhoneCodeInvalidError:
        print("❌ Invalid code. Try again.")
        await client.disconnect()
        return
    except Exception as e:
        print(f"❌ Auth error: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"✅ Logged in as: {me.phone or me.username or me.id}")

    # Pre-resolve all bot entities
    bot_ids = [8436907499, 7178305557, 6126376117, 8308748868, 6556421217,
               6832064371, 6113783210, 7060758339, 7294318663]
    for bid in bot_ids:
        try:
            await client.get_entity(bid)
            print(f"  ✅ Resolved bot: {bid}")
        except Exception:
            try:
                async for d in client.iter_dialogs(limit=100):
                    if d.id == bid:
                        print(f"  ✅ Found bot in dialogs: {d.name} ({d.id})")
                        break
            except Exception:
                pass

    await client.disconnect()
    print(f"\n✅ Session saved: {SESSION_FILE}.session")

    # Clean up state file
    try:
        os.remove(STATE_FILE)
    except Exception:
        pass

if __name__ == "__main__":
    asyncio.run(main())
