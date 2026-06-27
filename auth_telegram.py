#!/usr/bin/env python3
"""Delete old session, authorize with phone number, save fresh session."""
import asyncio
import os
import glob

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

async def main():
    # Clean old session
    for f in glob.glob(f"{SESSION_FILE}*"):
        try:
            os.remove(f)
            print(f"🗑️  Removed: {f}")
        except Exception as e:
            print(f"❌ Could not remove {f}: {e}")

    from telethon import TelegramClient, errors

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print("✅ Already authorized (old session still valid)")
        await client.disconnect()
        return

    print("📱 Sending code to", PHONE)
    sent = await client.send_code_request(PHONE)
    print(f"📤 Code sent via: {sent.type}")

    # Wait for user to provide OTP via stdin
    print("⌨️  Paste OTP code here: ", end="", flush=True)
    code = input().strip()

    try:
        await client.sign_in(PHONE, code)
        print("✅ Authorized successfully!")
    except errors.SessionPasswordNeededError:
        print("🔐 2FA password required:")
        pwd = input().strip()
        await client.sign_in(password=pwd)
        print("✅ Authorized with 2FA!")
    except Exception as e:
        print(f"❌ Auth error: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"✅ Logged in as: {me.phone or me.username or me.id}")
    
    # Pre-resolve bot entities
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

if __name__ == "__main__":
    asyncio.run(main())
