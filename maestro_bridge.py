import asyncio
import subprocess
import os
import sys

CODE_FILE = "/root/Opencode/otp_code.txt"
OUTPUT_FILE = "/root/Opencode/maestro_out.txt"

if os.path.exists(CODE_FILE):
    os.remove(CODE_FILE)
if os.path.exists(OUTPUT_FILE):
    os.remove(OUTPUT_FILE)

proc = subprocess.Popen(
    [sys.executable, "maestro_worker.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

def log(msg):
    with open(OUTPUT_FILE, "a") as f:
        f.write(msg + "\n")

waiting_for_code = False

import select
import time

start = time.time()
while time.time() - start < 120:
    if proc.poll() is not None:
        remaining = proc.stdout.read()
        log(remaining)
        break
    
    ready, _, _ = select.select([proc.stdout], [], [], 0.5)
    if ready:
        line = proc.stdout.readline()
        if line:
            log(line.strip())
            if "confirmation code" in line.lower():
                waiting_for_code = True
                break

if waiting_for_code:
    log("WAITING_FOR_CODE")
    start2 = time.time()
    while time.time() - start2 < 120:
        if os.path.exists(CODE_FILE):
            with open(CODE_FILE) as f:
                code = f.read().strip()
            if code:
                os.remove(CODE_FILE)
                proc.stdin.write(code + "\n")
                proc.stdin.flush()
                log(f"CODE_SENT: {code}")
                
                timeout = time.time() + 60
                while time.time() < timeout:
                    ready, _, _ = select.select([proc.stdout], [], [], 1)
                    if ready:
                        line = proc.stdout.readline()
                        if line:
                            log(line.strip())
                            if "logged in" in line.lower() or "sent" in line.lower() or "error" in line.lower():
                                break
                    elif proc.poll() is not None:
                        break
                break
        time.sleep(0.5)

proc.wait(timeout=30)
log(f"EXIT: {proc.returncode}")
