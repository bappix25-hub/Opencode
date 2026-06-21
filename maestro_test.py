"""Send hi to Maestro bot via Pyrogram userbot with pexpect."""
import pexpect
import sys
import time

CODE = sys.argv[1] if len(sys.argv) > 1 else None

if not CODE:
    print("Usage: python3 maestro_test.py <otp_code>")
    sys.exit(1)

child = pexpect.spawn('python3 -c \"from pyrogram import Client; import asyncio; app=Client(\\\"maestro_user\\\", api_id=26413354, api_hash=\\\"d0b3f351eea6bdd0623c75555430552c\\\", phone_number=\\\"+8801733446456\\\"); asyncio.run((app.connect(), print(\\\"CONNECTED\\\")))\"', timeout=60)

child.logfile = sys.stdout.buffer

idx = child.expect(['Enter confirmation code', 'CONNECTED', pexpect.TIMEOUT, pexpect.EOF], timeout=30)
print(f"\n--- State: {idx} ---")

if idx == 0:
    child.sendline(CODE)
    child.expect(pexpect.EOF, timeout=60)
    print(child.before.decode())
elif idx == 1:
    print("Already connected!")
