import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional
from telethon import TelegramClient

try:
    from learner import extract_launch_features, record_launch
except ImportError:
    extract_launch_features = None
    record_launch = None

logger = logging.getLogger("telegram_collector")

API_ID = 26413354
API_HASH = "d0b3f351eea6bdd0623c75555430552c"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maestro_session")

CHANNELS = [
    -1002122751413,  # Solana New Pool Alert
    -1002126036544,  # Solana LP Chat
]

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")

_client: Optional[TelegramClient] = None
_last_msg_ids: dict = {}
_tracked_tokens: dict = {}
_stats = {"scanned": 0, "new_tokens": 0, "errors": 0}
_ath_check_cache: dict = {}

GMGN_RE = re.compile(
    r'\*\*(?P<symbol>[^*]+)\*\*\s*\((?:\*\*)?(?P<name>[^(]+?)(?:\*\*)?\)\s*'
    r'.*?🎲 CA: `(?P<ca>[A-Za-z0-9]+)`'
    r'.*?💲 Price: \*\*(?P<price>[^*]+)\*\*'
    r'.*?🎯 Dex: \*\*(?P<dex>[^*]+)\*\*'
    r'.*?💡 MCP: \*\*(?P<mcp>[^*]+)\*\*'
    r'.*?💧 Liq池子: \*\$(?P<liq_usd>[0-9,.]+)\*\*.*?\((?:\*\*)?(?P<liq_sol>[0-9,.]+) SOL'
    r'.*?💰 Initial LP底池: \*\*(?P<initial_lp>[^%]+)%\*\*'
    r'.*?👥 Holder持有人: \*\*(?P<holders>\d+)\*\*'
    r'.*?👤 Renounced已弃权: (?P<renounced>[✅❌]+)'
    r'.*?Balance SOL: (?P<dev_balance_sol>[0-9,.]+)',
    re.DOTALL
)

def load_tracked():
    global _tracked_tokens
    try:
        with open(DATA_FILE) as f:
            _tracked_tokens = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _tracked_tokens = {}

def save_tracked():
    with open(DATA_FILE, "w") as f:
        json.dump(_tracked_tokens, f, indent=2, default=str)

def parse_gmgn_msg(text: str) -> Optional[dict]:
    m = GMGN_RE.search(text)
    if not m:
        return None
    try:
        mcp_str = m.group("mcp").replace(",", "").strip()
        mcp = float(mcp_str) if mcp_str else 0
    except ValueError:
        mcp = 0
    try:
        liq_usd_str = m.group("liq_usd").replace(",", "")
        liq_usd = float(liq_usd_str) if liq_usd_str else 0
    except ValueError:
        liq_usd = 0
    try:
        liq_sol_str = m.group("liq_sol").replace(",", "")
        liq_sol = float(liq_sol_str) if liq_sol_str else 0
    except ValueError:
        liq_sol = 0
    try:
        holders = int(m.group("holders"))
    except ValueError:
        holders = 0
    try:
        initial_lp = float(m.group("initial_lp"))
    except ValueError:
        initial_lp = 0
    try:
        dev_bal = float(m.group("dev_balance_sol").replace(",", ""))
    except ValueError:
        dev_bal = 0
    return {
        "symbol": m.group("symbol").strip(),
        "name": m.group("name").strip(),
        "ca": m.group("ca"),
        "dex": m.group("dex").strip(),
        "mcp": mcp,
        "liq_usd": liq_usd,
        "liq_sol": liq_sol,
        "initial_lp_pct": initial_lp,
        "holders": holders,
        "renounced": "✅" in m.group("renounced"),
        "dev_balance_sol": dev_bal,
        "first_seen": datetime.now(timezone.utc).timestamp(),
        "launch_mcp": mcp,
        "launch_liq": liq_usd,
        "ath_mcp": mcp,
        "ath_multiplier": 1.0,
        "status": "tracking",
        "last_check": datetime.now(timezone.utc).timestamp(),
    }

async def get_client() -> Optional[TelegramClient]:
    global _client
    if _client is None:
        _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await _client.connect()
        if not await _client.is_user_authorized():
            logger.error("Telegram client not authorized")
            return None
    return _client

async def scan_channels(client: TelegramClient):
    global _stats
    for cid in CHANNELS:
        try:
            entity = await client.get_entity(cid)
            last_id = _last_msg_ids.get(cid, 0)
            msgs = await client.get_messages(entity, limit=20)
            if not msgs:
                continue
            for msg in msgs:
                if msg.id <= last_id:
                    continue
                if not msg.text:
                    continue
                token = parse_gmgn_msg(msg.text)
                if not token:
                    continue
                _stats["scanned"] += 1
                ca = token["ca"]
                if ca not in _tracked_tokens:
                    _tracked_tokens[ca] = token
                    _stats["new_tokens"] += 1
                    logger.info(f"[NEW] {token['symbol']} MCP=${token['mcp']:.0f} liq=${token['liq_usd']:.0f} holders={token['holders']}")
                else:
                    existing = _tracked_tokens[ca]
                    existing["last_check"] = datetime.now(timezone.utc).timestamp()
            _last_msg_ids[cid] = msgs[0].id
        except Exception as e:
            _stats["errors"] += 1
            logger.debug(f"Channel {cid} scan error: {e}")

async def check_ath(dex_client, address: str) -> tuple:
    if address in _ath_check_cache:
        last_check, result = _ath_check_cache[address]
        if datetime.now(timezone.utc).timestamp() - last_check < 300:
            return result
    try:
        pair = await dex_client.fetch_pair_data(address)
        if not pair:
            return None, None
        price_str = pair.get("priceUsd", "0")
        current_price = float(price_str) if price_str else 0
        fdv = float(pair.get("fdv", 0) or 0)
        _ath_check_cache[address] = (datetime.now(timezone.utc).timestamp(), (current_price, fdv))
        return current_price, fdv
    except:
        return None, None

async def run_ath_checks(dex_client):
    now = datetime.now(timezone.utc).timestamp()
    for ca, token in list(_tracked_tokens.items()):
        if now - token.get("last_check", 0) > 300:
            current_price, current_mcp = await check_ath(dex_client, ca)
            if current_mcp and current_mcp > 0:
                token["last_check"] = now
                if current_mcp > token.get("ath_mcp", 0):
                    token["ath_mcp"] = current_mcp
                    token["ath_multiplier"] = current_mcp / max(token["launch_mcp"], 1)
                    multiplier = token["ath_multiplier"]
                    if multiplier >= 50:
                        token["status"] = "mega_winner"
                        logger.info(f"[MEGA WINNER] {token['symbol']} x{multiplier:.0f} (${token['launch_mcp']:.0f}→${current_mcp:.0f})")
                        _sync_winner(token)
                    elif multiplier >= 5:
                        if token.get("status") != "mega_winner":
                            token["status"] = "winner"
                            logger.info(f"[WINNER] {token['symbol']} x{multiplier:.1f}")
                            _sync_winner(token)
                elif current_mcp < token["launch_mcp"] * 0.3 and token.get("status") == "tracking":
                    token["status"] = "loser"

async def run_loop(dex_client, interval: int = 15):
    logger.info("Starting Telegram collector loop...")
    client = await get_client()
    if not client:
        logger.error("Cannot start collector - no client")
        return
    load_tracked()
    while True:
        try:
            await scan_channels(client)
            await run_ath_checks(dex_client)
            save_tracked()
            tracked = len(_tracked_tokens)
            winners = sum(1 for t in _tracked_tokens.values() if t.get("status") in ("winner", "mega_winner"))
            losers = sum(1 for t in _tracked_tokens.values() if t.get("status") == "loser")
            if tracked > 0:
                logger.info(f"[COLLECTOR] {tracked} tracked | {winners} winners | {losers} losers | {_stats['new_tokens']} new this session")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Collector error: {e}")
        await asyncio.sleep(interval)

def get_tracked_tokens():
    return _tracked_tokens

def get_winners(min_mult: float = 5.0):
    return {ca: t for ca, t in _tracked_tokens.items() if t.get("ath_multiplier", 0) >= min_mult}

def get_stats():
    return {
        "tracked": len(_tracked_tokens),
        "winners": sum(1 for t in _tracked_tokens.values() if t.get("status") in ("winner", "mega_winner")),
        "losers": sum(1 for t in _tracked_tokens.values() if t.get("status") == "loser"),
        "new_this_session": _stats["new_tokens"],
        "scanned": _stats["scanned"],
    }

BOT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")

def _sync_winner(token: dict):
    try:
        ca = token["ca"]
        with open(BOT_DATA_FILE) as f:
            data = json.load(f)
        sr = data.setdefault("model", {}).setdefault("signal_results", [])
        for entry in sr:
            if entry.get("address") == ca:
                return
        now_str = datetime.now(timezone.utc).isoformat()
        sr.append({
            "address": ca,
            "symbol": token["symbol"],
            "name": token["name"],
            "ath_multiplier": round(token["ath_multiplier"], 2),
            "launch_mcap": token["launch_mcp"],
            "peak_mcap": token["ath_mcp"],
            "launch_liq": token["launch_liq"],
            "holders": token["holders"],
            "is_pump": True,
            "score": 0.9 if token["ath_multiplier"] >= 50 else 0.75,
            "verdict": "MEGA_PUMP" if token["ath_multiplier"] >= 50 else "PUMP",
            "detected_at": now_str,
        })
        with open(BOT_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[SYNC] Added {token['symbol']} x{token['ath_multiplier']:.1f} to signal_results")
    except Exception as e:
        logger.debug(f"Sync winner error: {e}")
