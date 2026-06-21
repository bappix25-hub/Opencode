import pexpect
import sys

code = sys.argv[1]
child = pexpect.spawn('python3', ['maestro_worker.py'], timeout=120, encoding='utf-8')
child.logfile = sys.stdout

idx = child.expect(['Enter confirmation code', 'Sent', 'Error', pexpect.TIMEOUT, pexpect.EOF], timeout=30)
if idx == 0:
    child.sendline(code)
    child.expect([pexpect.EOF, pexpect.TIMEOUT], timeout=90)
else:
    print(f"State: {idx}")
