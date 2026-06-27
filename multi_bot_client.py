"""
multi_bot_client.py — Parallel multi-bot token scanner with rotation.
Uses 9 Telegram bots to get token data. Rotates to avoid rate limits.

Bots:
  - TokenScan × 3 (holders, top10, bundled, audit, dev, socials)
  - Rick × 2 (holders, top10, renounced, freeze, dev)
  - Phanes × 2 (fresh_1d/7d, top10, dev_sold, dex_paid)
  - Extra × 2 (security, holders, liquidity)
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("multi_bot_client")

# === BOT REGISTRY ===
BOTS = {
    # TokenScan-like: holders, top10%, bundled%, audit, dev, socials
    "tokenscan": [8436907499, 7178305557, 6126376117, 8308748868, 6556421217],
    # Rick-like: holders, top10%, renounced, freeze_revoked, dev_status
    "rick": [6832064371, 6113783210],
    # Phanes-like: fresh_1d/7d, top10%, dev_sold, dex_paid
    "phanes": [7060758339, 7294318663],
    # Extra security/overview bots
    "extra": [],
}

# Combined all bot IDs for rotation
ALL_BOT_IDS = [bid for bots in BOTS.values() for bid in bots if bid]

_bot_last_used = {}  # bot_id -> timestamp
_MIN_INTERVAL = 3.5  # seconds between calls to same bot

_TOKENSCAN_PARSERS = ["tokenscan"]
_RICK_PARSERS = ["rick"]
_PHANES_PARSERS = ["phanes"]


def _next_bot(bot_type: str = None) -> int:
    """Get next available bot by type, or any if type is None."""
    global _MIN_INTERVAL
    now = time.time()

    if bot_type:
        candidates = BOTS.get(bot_type, ALL_BOT_IDS)
    else:
        candidates = ALL_BOT_IDS

    if not candidates:
        return 0

    # Try to find a bot that hasn't been used recently
    best_bot = candidates[0]
    best_wait = float("inf")

    for bot_id in candidates:
        last_used = _bot_last_used.get(bot_id, 0)
        wait = max(0, _MIN_INTERVAL - (now - last_used))
        if wait <= 0:
            _bot_last_used[bot_id] = now
            return bot_id
        if wait < best_wait:
            best_wait = wait
            best_bot = bot_id

    # All bots busy, use least recently used
    _bot_last_used[best_bot] = now
    return best_bot


async def send_and_read(client, bot_id: int, address: str, timeout: int = 12) -> str:
    """Send token address to bot and read response."""
    from telethon import errors as t_errors

    if not client or not client.is_connected():
        return ""

    try:
        await client.send_message(bot_id, address)
        await asyncio.sleep(3)

        messages = await client.get_messages(bot_id, limit=5)
        for msg in messages:
            if msg and msg.text and (address[:8] in msg.text or address[-8:] in msg.text):
                return msg.text

        # Retry with longer wait
        await asyncio.sleep(4)
        messages = await client.get_messages(bot_id, limit=5)
        for msg in messages:
            if msg and msg.text and (address[:8] in msg.text or address[-8:] in msg.text):
                return msg.text

        return ""
    except t_errors.FloodWaitError as e:
        logger.warning(f"Bot {bot_id} flood wait: {e.seconds}s")
        return ""
    except Exception as e:
        logger.debug(f"Bot {bot_id} error: {e}")
        return ""


def _parse_number(s: str) -> float:
    """Parse formatted number like '34.36K' or '1,234'."""
    s = s.strip().replace(",", "")
    multiplier = 1.0
    if s.endswith("K"):
        multiplier = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.endswith("B"):
        multiplier = 1_000_000_000
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return 0.0


def _clean_text(text: str) -> str:
    """Remove Telegram markdown formatting."""
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    return text


def _parse_tokenscan(text: str) -> dict:
    """Parse TokenScan-style response (holders, top10, bundled, audit, socials)."""
    result = {"holders": 0, "top10_pct": 0.0, "bundled_pct": 0.0,
              "audit_score": 0, "audit_max": 10, "dex_paid": False,
              "mc": 0.0, "ath": 0.0, "liq": 0.0, "vol_24h": 0.0,
              "dev_wallet": "", "socials": [], "websites": [],
              "has_website": False, "has_twitter": False, "has_telegram": False}

    if not text:
        return result

    text = _clean_text(text)

    # MC, ATH, LIQ, VOL
    for key in ["MC", "ATH", "LIQ", "VOL"]:
        m = re.search(rf'{key}:\s*\$?([\d,.]+[KMB]?)', text)
        if m:
            val = _parse_number(m.group(1))
            if key == "MC": result["mc"] = val
            elif key == "ATH": result["ath"] = val
            elif key == "LIQ": result["liq"] = val
            elif key == "VOL": result["vol_24h"] = val

    # HLD
    m = re.search(r'HLD:\s*([\d,]+)', text)
    if m:
        result["holders"] = int(m.group(1).replace(",", ""))

    # Top 10
    m = re.search(r'(?:Top\s*10|top10)\s*[\[:]?\s*([\d.]+)%', text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if v <= 100: result["top10_pct"] = v
        except: pass

    # Bundled
    m = re.search(r'Bundled\s*[\[:]?\s*([\d.]+)%', text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if v <= 100: result["bundled_pct"] = v
        except: pass

    # Audit
    m = re.search(r'Audit\s+(\d+)/(\d+)', text)
    if m:
        result["audit_score"] = int(m.group(1))
        result["audit_max"] = int(m.group(2))

    # DEX PAID
    result["dex_paid"] = "DEX [PAID]" in text or "DEX[PAID]" in text or "DEX PAID" in text.upper()

    # DEV wallet
    m = re.search(r'DEV:\s*([A-Za-z0-9]{10,})', text)
    if m:
        result["dev_wallet"] = m.group(1)

    # Socials
    urls = re.findall(r'https?://[^\s<>"\')\]>]+', text)
    for url in urls:
        if 'twitter.com' in url or 'x.com' in url:
            result["socials"].append({"url": url, "type": "twitter"})
            result["has_twitter"] = True
        elif 't.me' in url:
            result["socials"].append({"url": url, "type": "telegram"})
            result["has_telegram"] = True
        elif 'dexscreener' not in url and 'gmgn' not in url:
            result["websites"].append({"url": url})
            result["has_website"] = True

    # Website: and Twitter: labels
    m = re.search(r'Website:\s*(https?://[^\s<>"\']+)', text, re.IGNORECASE)
    if m: result["has_website"] = True

    m = re.search(r'Twitter:\s*(https?://[^\s<>"\']+)', text, re.IGNORECASE)
    if m: result["has_twitter"] = True

    return result


def _parse_rick(text: str) -> dict:
    """Parse Rick-style response (holders, top10, renounced, freeze, dev)."""
    result = {"holders": 0, "top10_pct": 0.0, "renounced": False,
              "freeze_revoked": False, "dev_status": "UNKNOWN", "lp_locked_pct": 0.0}

    if not text:
        result["has_rick_data"] = False
        return result

    text = _clean_text(text)
    result["has_rick_data"] = True

    # Holders
    m = re.search(r'(?:Holders?|HLD|holders?)[:\s]*([\d,]+)', text, re.IGNORECASE)
    if m:
        result["holders"] = int(m.group(1).replace(",", ""))

    # Top 10
    m = re.search(r'(?:Top\s*10|top10)[:\s]*([\d.]+)%', text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if v <= 100: result["top10_pct"] = v
        except: pass

    # Renounced
    result["renounced"] = any(w in text.lower() for w in ["renounced", "✅ yes", "renounce: yes"])
    result["renounced"] = result["renounced"] and "not renounced" not in text.lower()
    if "not renounced" in text.lower() or "renounce: no" in text.lower() or "❌" in text[:500]:
        result["renounced"] = False

    # Freeze revoked
    result["freeze_revoked"] = "freeze revoked" in text.lower() or "✅" in text[500:1000] if len(text) > 500 else False

    # Dev status
    if "dev renounced" in text.lower() or "renounced" in text.lower():
        result["dev_status"] = "RENOUNCED"
    elif "dev active" in text.lower() or "not renounced" in text.lower():
        result["dev_status"] = "ACTIVE"
    else:
        result["dev_status"] = "UNKNOWN"

    # LP locked
    m = re.search(r'(?:LP|Liquidity)[:\s]*([\d.]+)%', text, re.IGNORECASE)
    if m:
        try:
            result["lp_locked_pct"] = float(m.group(1))
        except: pass

    return result


def _parse_phanes(text: str) -> dict:
    """Parse Phanes-style response (fresh_1d/7d, top10, dev_sold, dex_paid)."""
    result = {"fresh_1d": 0, "fresh_7d": 0, "top10_pct": 0.0,
              "dev_sold": False, "dex_paid": False, "holders": 0}

    if not text:
        result["has_phanes_data"] = False
        return result

    text = _clean_text(text)
    result["has_phanes_data"] = True

    # Fresh 1d / 7d
    m = re.search(r'(?:Fresh|New)\s*(?:1[dD]|24h)[:\s]*([\d,.]+)%', text)
    if m:
        try: result["fresh_1d"] = float(m.group(1).replace(",", ""))
        except: pass
    m = re.search(r'(?:Fresh|New)\s*(?:7[dD])[:\s]*([\d,.]+)%', text)
    if m:
        try: result["fresh_7d"] = float(m.group(1).replace(",", ""))
        except: pass

    # Top 10
    m = re.search(r'(?:Top\s*10|top10)[:\s]*([\d.]+)%', text, re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if v <= 100: result["top10_pct"] = v
        except: pass

    # Dev sold
    result["dev_sold"] = "dev sold" in text.lower() or "dev_sold" in text.lower()

    # DEX paid
    result["dex_paid"] = "dex paid" in text.lower() or "dex_paid" in text.lower() or "dex [paid]" in text.lower()

    # Holders
    m = re.search(r'(?:Holders?|HLD)[:\s]*([\d,]+)', text, re.IGNORECASE)
    if m:
        result["holders"] = int(m.group(1).replace(",", ""))

    return result


async def scan_all(client, address: str) -> dict:
    """
    Scan a token address using ALL bots with rotation.
    Returns merged data from all sources.
    """
    merged = {
        "holders": 0, "top10_pct": 0.0, "bundled_pct": 0.0,
        "audit_score": 0, "audit_max": 10, "dex_paid": False,
        "mc": 0.0, "liq": 0.0, "vol_24h": 0.0, "ath": 0.0,
        "dev_wallet": "", "dev_status": "UNKNOWN",
        "renounced": False, "freeze_revoked": False,
        "fresh_1d": 0, "fresh_7d": 0, "dev_sold": False,
        "socials": [], "websites": [],
        "has_website": False, "has_twitter": False, "has_telegram": False,
        "lp_locked_pct": 0.0,
        "sources": [],  # which bots responded
    }

    if not client or not client.is_connected():
        logger.warning("Telegram client not available for multi-bot scan")
        return merged

    # Scan TokenScan bots (5 bots)
    ts_bot = _next_bot("tokenscan")
    if ts_bot:
        text = await send_and_read(client, ts_bot, address)
        if text:
            data = _parse_tokenscan(text)
            merged.update({k: v for k, v in data.items() if v})
            merged["sources"].append(f"ts_{ts_bot}")

    # Scan Rick bots (2 bots)
    rick_bot = _next_bot("rick")
    if rick_bot:
        text = await send_and_read(client, rick_bot, address)
        if text:
            data = _parse_rick(text)
            for k in ["holders", "top10_pct", "renounced", "freeze_revoked", "dev_status", "lp_locked_pct"]:
                if data.get(k): merged[k] = data[k]
            merged["sources"].append(f"rick_{rick_bot}")

    # Scan Phanes bots (2 bots)
    ph_bot = _next_bot("phanes")
    if ph_bot:
        text = await send_and_read(client, ph_bot, address)
        if text:
            data = _parse_phanes(text)
            for k in ["fresh_1d", "fresh_7d", "top10_pct", "dev_sold", "dex_paid", "holders"]:
                if data.get(k) or k in ("dex_paid", "dev_sold"):
                    merged[k] = data[k]
            merged["sources"].append(f"ph_{ph_bot}")

    # Scan extra bots (round-robin any)
    extra_bot = _next_bot("extra") if BOTS["extra"] else None
    if extra_bot:
        text = await send_and_read(client, extra_bot, address)
        if text:
            # Try all parsers
            data = _parse_tokenscan(text)
            if data.get("holders") or data.get("top10_pct"):
                merged.update({k: v for k, v in data.items() if v})
                merged["sources"].append(f"extra_ts_{extra_bot}")
            else:
                data = _parse_rick(text)
                if data.get("has_rick_data"):
                    for k in ["holders", "top10_pct", "renounced", "dev_status"]:
                        if data.get(k): merged[k] = data[k]
                    merged["sources"].append(f"extra_rick_{extra_bot}")

    return merged
