#!/usr/bin/env python3
"""Auth Step 1: Delete old session, send code request, save state."""
import asyncio
import os
import glob
import json

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
PHONE = "+8801733446456"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

async def main():
    # Clean old session files
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
        print("✅ Already authorized")
        await client.disconnect()
        return

    print("📱 Sending code to", PHONE)
    sent = await client.send_code_request(PHONE)
    print(f"📤 Code sent via: {sent.type}")
    print("📤 Type: SMS" if "sms" in str(sent.type).lower() else "📤 Type: Telegram")

    # Save phone_hash for step 2
    state = {"phone": PHONE, "phone_code_hash": sent.phone_code_hash}
    state_file = SESSION_FILE + "_auth_state.json"
    with open(state_file, "w") as f:
        json.dump(state, f)
    print(f"💾 State saved to {state_file}")
    print(f"\n📱 ===== CHECK TELEGRAM FOR THE CODE =====")
    print(f"📱 Paste it when I ask you!")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
