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

SYNC_DIRS = [
    "backtest_reports",
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

    for d in SYNC_DIRS:
        if os.path.isdir(d):
            code, _, stderr = await _run_git(["git", "add", d])
            if code != 0:
                logger.warning(f"git add {d} failed: {stderr}")

    code, stdout, stderr = await _run_git(["git", "commit", "-m", message])
    if "nothing to commit" in stdout or "nothing to commit" in stderr:
        logger.info("GitHub: কোনো পরিবর্তন নেই")
        return True
    if code != 0 and "nothing to commit" not in stderr:
        logger.warning(f"git commit issue: {stderr}")

    env = os.environ.copy()
    if GITHUB_PAT:
        env["GIT_TERMINAL_PROMPT"] = "0"
        import shutil
        env["GIT_ASKPASS"] = shutil.which("true") or "/usr/bin/true"
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

def _smart_merge_data_file(local_path: str, remote_path: str, output_path: str) -> bool:
    """3-way merge: combine pump_patterns + dump_patterns + trained_addresses from both files.
    Keeps ALL learned data — never overwrites."""
    import json
    try:
        with open(local_path) as f:
            local = json.load(f)
    except Exception:
        return False
    try:
        with open(remote_path) as f:
            remote = json.load(f)
    except Exception:
        remote = {}

    def _merge_list(local_list, remote_list, key="address", cap=100):
        seen = set()
        out = []
        for item in (remote_list or []) + (local_list or []):
            if isinstance(item, str):
                if item in seen:
                    continue
                seen.add(item)
                out.append(item)
            elif isinstance(item, dict):
                k = item.get(key)
                if k and k in seen:
                    continue
                if k:
                    seen.add(k)
                out.append(item)
            else:
                out.append(item)
        return out[:cap]

    def _merge_dict(a, b):
        if isinstance(a, list) and isinstance(b, list):
            return list(dict.fromkeys(a + b))
        return {**(a or {}), **(b or {})}

    def _merge_list_simple(a, b):
        if isinstance(a, list) and isinstance(b, list):
            combined = a + b
            if combined and isinstance(combined[0], dict):
                seen = set()
                out = []
                for item in combined:
                    k = item.get("address", item.get("symbol", str(item)))
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append(item)
                return out
            return list(dict.fromkeys(combined))
        return a or b or []

    merged = dict(remote)
    merged["pump_patterns"] = _merge_list(
        local.get("pump_patterns"),
        remote.get("pump_patterns"),
        key="address", cap=100,
    )
    merged["dump_patterns"] = _merge_list(
        local.get("dump_patterns"),
        remote.get("dump_patterns"),
        key="address", cap=100,
    )
    merged["launch_patterns"] = _merge_list(
        local.get("launch_patterns"),
        remote.get("launch_patterns"),
        key="address", cap=100,
    )
    merged["trained_addresses"] = _merge_list_simple(
        local.get("trained_addresses"),
        remote.get("trained_addresses"),
    )
    merged["signals"] = _merge_list(
        local.get("signals"),
        remote.get("signals"),
        key="address", cap=200,
    )
    merged["launches_tracked"] = _merge_list(
        local.get("launches_tracked"),
        remote.get("launches_tracked"),
        key="address", cap=500,
    )
    for k in ("signal_results", "alerted_coins", "golden_snapshots"):
        merged[k] = _merge_list(
            local.get(k),
            remote.get(k),
            key="address", cap=200,
        )
    for k in ("blacklisted", "honeypot_addresses", "blocked_deployers"):
        if k in local or k in remote:
            merged[k] = _merge_list_simple(local.get(k), remote.get(k))
    if "model" in local or "model" in remote:
        r_model = remote.get("model", {})
        l_model = local.get("model", {})
        merged_model = dict(r_model)
        for mk, mv in l_model.items():
            if mk == "signal_results":
                existing = merged_model.get("signal_results", [])
                merged_model["signal_results"] = _merge_list(
                    mv, existing, key="address", cap=500,
                )
            elif mv and (mk not in merged_model or not merged_model[mk]):
                merged_model[mk] = mv
        merged["model"] = merged_model
    for k, v in local.items():
        if k not in merged:
            merged[k] = v

    try:
        with open(output_path, "w") as f:
            json.dump(merged, f, indent=2)
        logger.info(
            f"Smart-merge: {len(merged['pump_patterns'])} pumps, "
            f"{len(merged['dump_patterns'])} dumps, "
            f"{len(merged.get('signals', []))} signals, "
            f"{len(merged.get('launches_tracked', []))} launches, "
            f"{len(merged['trained_addresses'])} addrs"
        )
        return True
    except Exception as e:
        logger.error(f"Smart-merge write failed: {e}")
        return False


async def restore_from_github() -> bool:
    if not await _configure_remote():
        return False
    if not await _ensure_branch():
        return False

    for f in [DATA_FILE, GOLDEN_FILE, BLACKLIST_FILE]:
        if os.path.exists(f):
            try:
                import shutil
                shutil.copy2(f, f"{f}.bak")
            except Exception:
                pass

    code, _, _ = await _run_git(["git", "fetch", "origin", GIT_BRANCH], timeout=60)
    if code != 0:
        logger.warning("git fetch failed, continuing with local data")

    code, _, _ = await _run_git(["git", "merge", "--abort"], timeout=10)
    code, _, _ = await _run_git(["git", "rebase", "--abort"], timeout=10)

    code, _, _ = await _run_git(["git", "checkout", "origin/" + GIT_BRANCH, "--", DATA_FILE], timeout=30)
    if code == 0 and os.path.exists(f"{DATA_FILE}.bak"):
        tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        remote_data = os.path.join(tmp_dir, f"remote_data_{os.getpid()}.json")
        import shutil
        try:
            shutil.copy2(DATA_FILE, remote_data)
            _smart_merge_data_file(f"{DATA_FILE}.bak", remote_data, DATA_FILE)
            _run_git_sync = False
        except Exception as e:
            logger.warning(f"Smart-merge step failed, keeping remote: {e}")
        finally:
            try:
                os.remove(remote_data)
            except Exception:
                pass

    code, stdout, stderr = await _run_git(
        ["git", "checkout", "origin/" + GIT_BRANCH, "--"] +
        [f for f in [GOLDEN_FILE, BLACKLIST_FILE] if f],
        timeout=30
    )

    code, _, _ = await _run_git(
        ["git", "reset", "origin/" + GIT_BRANCH],
        timeout=10
    )

    logger.info(f"GitHub থেকে ডেটা রিস্টোর হয়েছে (smart-merge) [{GIT_BRANCH}]")
    for f in [DATA_FILE, GOLDEN_FILE, BLACKLIST_FILE]:
        try:
            if os.path.exists(f"{f}.bak"):
                os.remove(f"{f}.bak")
        except Exception:
            pass
    return True
