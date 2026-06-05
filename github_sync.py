import asyncio
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("github_sync")

BOT_INSTANCE = os.environ.get("BOT_INSTANCE", "main")
GIT_BRANCH = os.environ.get("GIT_BRANCH", "main" if BOT_INSTANCE == "main" else f"{BOT_INSTANCE}-data")
DATA_FILE = os.environ.get("DATA_FILE", "./bot_data.json")
GOLDEN_FILE = "./golden_patterns.json"
BLACKLIST_FILE = "./blacklist_patterns.json"
GITHUB_PAT = os.environ.get("GITHUB_PAT", "").strip()
GITHUB_USER = os.environ.get("GITHUB_USER", "bappix25-hub").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Opencode").strip()

BASE_REMOTE_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
if GITHUB_PAT:
    REMOTE_URL = f"https://{GITHUB_USER}:{GITHUB_PAT}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
else:
    REMOTE_URL = BASE_REMOTE_URL

async def _run_git(args: list, timeout: int = 30) -> tuple:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd()
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
    except asyncio.TimeoutError:
        logger.error(f"Git command timed out: {' '.join(args)}")
        return -1, "", "timeout"
    except Exception as e:
        logger.error(f"Git command error: {e}")
        return -1, "", str(e)

async def _configure_remote() -> bool:
    code, _, _ = await _run_git(["git", "remote", "get-url", "origin"])
    if code != 0:
        code, _, stderr = await _run_git(["git", "remote", "add", "origin", REMOTE_URL])
        if code != 0:
            logger.warning(f"Remote add failed: {stderr}")
            return False
        logger.info(f"Remote added (PAT={'yes' if GITHUB_PAT else 'no'})")
    else:
        code, current_url, _ = await _run_git(["git", "remote", "get-url", "origin"])
        if current_url != REMOTE_URL:
            code, _, stderr = await _run_git(["git", "remote", "set-url", "origin", REMOTE_URL])
            if code != 0:
                logger.warning(f"Remote update failed: {stderr}")
                return False
            logger.info(f"Remote updated (PAT={'yes' if GITHUB_PAT else 'no'})")
    return True

async def _ensure_branch() -> bool:
    code, _, stderr = await _run_git(["git", "rev-parse", "--verify", GIT_BRANCH])
    if code != 0:
        code, _, stderr = await _run_git(["git", "checkout", "-b", GIT_BRANCH])
        if code != 0:
            logger.warning(f"Branch create failed: {stderr}")
            return False
    else:
        code, _, stderr = await _run_git(["git", "checkout", GIT_BRANCH])
        if code != 0:
            logger.warning(f"Checkout failed: {stderr}")
            return False
    return True

SYNC_FILES = [
    "meme_bot.py", "learner.py", "github_sync.py", DATA_FILE,
    ".env.example", "config.py", "bot_state.py", "dex_client.py",
    "rugcheck_client.py", "helius_client.py", "pumpportal_ws.py",
    "telegram_bot.py", "utils.py", "signal_filter.py", "social_signals.py",
    "verify_loop.py", "backtest.py", "paper_trader.py", "honeypot_detector.py",
    GOLDEN_FILE, BLACKLIST_FILE
]

async def sync_to_github(message: str = None) -> bool:
    if not message:
        message = f"[{BOT_INSTANCE}] auto sync {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"

    if not await _configure_remote():
        return False
    if not await _ensure_branch():
        return False

    for f in SYNC_FILES:
        if os.path.exists(f):
            code, _, stderr = await _run_git(["git", "add", f])
            if code != 0:
                logger.warning(f"git add {f} failed: {stderr}")

    code, stdout, stderr = await _run_git(["git", "commit", "-m", message])
    if "nothing to commit" in stdout or "nothing to commit" in stderr:
        logger.info("GitHub: কোনো পরিবর্তন নেই")
        return True
    if code != 0 and "nothing to commit" not in stderr:
        logger.warning(f"git commit issue: {stderr}")

    env = os.environ.copy()
    if GITHUB_PAT:
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "/bin/true"
    proc = await asyncio.create_subprocess_exec(
        "git", "push", "origin", GIT_BRANCH,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.getcwd(),
        env=env
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        code = proc.returncode
        stdout = stdout.decode().strip()
        stderr = stderr.decode().strip()
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("GitHub push timeout")
        return False
    if code == 0:
        logger.info(f"GitHub sync সফল [{GIT_BRANCH}]: {message}")
        return True
    else:
        logger.error(f"GitHub push এরর: {stderr or stdout}")
        return False

async def restore_from_github() -> bool:
    if not await _configure_remote():
        return False
    if not await _ensure_branch():
        return False
    code, stdout, stderr = await _run_git(["git", "pull", "origin", GIT_BRANCH], timeout=60)
    if code == 0:
        logger.info(f"GitHub থেকে ডেটা রিস্টোর হয়েছে [{GIT_BRANCH}]")
        return True
    logger.error(f"Restore এরর: {stderr}")
    return False
