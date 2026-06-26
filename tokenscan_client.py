"""
tokenscan_client.py — Use multiple Telegram bots for token analysis.

TokenScan provides:
- Holders count
- Top 10 Holders %
- Bundled %
- Audit score (x/10)
- DEX paid status
- Dev wallet address
- ATH with timestamp

Uses 3 bots rotated to avoid rate limits:
- @tokenscan (8436907499)
- Bot 2 (7178305557)
- Bot 3 (6126376117)
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("tokenscan_client")

# Multiple bots for rotation (avoids rate limits)
TOKENSCAN_BOTS = [8436907499, 7178305557, 6126376117]
_bot_index = 0
_bot_last_used = {}
_MIN_INTERVAL = 3  # seconds between uses per bot

TIMEOUT = 15


def _next_bot() -> int:
    """Get next available bot (round-robin with rate limit)."""
    global _bot_index
    now = time.time()
    for _ in range(len(TOKENSCAN_BOTS)):
        bot_id = TOKENSCAN_BOTS[_bot_index % len(TOKENSCAN_BOTS)]
        _bot_index += 1
        last_used = _bot_last_used.get(bot_id, 0)
        if now - last_used >= _MIN_INTERVAL:
            _bot_last_used[bot_id] = now
            return bot_id
    # All bots recently used, pick least recent
    least_recent = min(TOKENSCAN_BOTS, key=lambda b: _bot_last_used.get(b, 0))
    _bot_last_used[least_recent] = now
    return least_recent


def _parse_tokenscan_response(text: str) -> dict:
    """Parse TokenScan bot response into structured data."""
    result = {
        "holders": 0,
        "top10_pct": 0.0,
        "bundled_pct": 0.0,
        "audit_score": 0,
        "audit_max": 10,
        "dex_paid": False,
        "ath": 0.0,
        "mc": 0.0,
        "liq": 0.0,
        "vol_24h": 0.0,
        "price_change_1h_buys": 0,
        "price_change_1h_sells": 0,
        "dev_wallet": "",
        "parsed": True,
    }

    if not text:
        result["parsed"] = False
        return result

    # Strip Telegram markdown
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # MC: $34.36K
    m = re.search(r'MC:\s*\$?([\d,.]+[KMB]?)', text)
    if m:
        result["mc"] = _parse_number(m.group(1))

    # ATH: $50.6K (-32.09% / 2m)
    m = re.search(r'ATH:\s*\$?([\d,.]+[KMB]?)', text)
    if m:
        result["ath"] = _parse_number(m.group(1))

    # LIQ: $12.95K
    m = re.search(r'LIQ:\s*\$?([\d,.]+[KMB]?)', text)
    if m:
        result["liq"] = _parse_number(m.group(1))

    # VOL: $114.37K (24h)
    m = re.search(r'VOL:\s*\$?([\d,.]+[KMB]?)', text)
    if m:
        result["vol_24h"] = _parse_number(m.group(1))

    # 1H: B 1,486 / S 1,202 (23.63%)
    m = re.search(r'1H:\s*B\s*([\d,]+)\s*/\s*S\s*([\d,]+)', text)
    if m:
        result["price_change_1h_buys"] = int(m.group(1).replace(",", ""))
        result["price_change_1h_sells"] = int(m.group(2).replace(",", ""))

    # HLD: 336
    m = re.search(r'HLD:\s*([\d,]+)', text)
    if m:
        result["holders"] = int(m.group(1).replace(",", ""))

    # Top 10 Holders [21.5%] or top10 21.5% or Top 10 21.5%
    m = re.search(r'(?:Top\s*10\s*Holders?\s*\[)?([\d.]+)%\]?', text, re.IGNORECASE)
    if m:
        try:
            val = float(m.group(1))
            if val <= 100:
                result["top10_pct"] = val
        except ValueError:
            pass

    # Bundled [9.86%] or Bundled 9.86%
    m = re.search(r'Bundled\s*\[?([\d.]+)%\]?', text, re.IGNORECASE)
    if m:
        try:
            val = float(m.group(1))
            if val <= 100:
                result["bundled_pct"] = val
        except ValueError:
            pass

    # Audit 8/10
    m = re.search(r'Audit\s+(\d+)/(\d+)', text)
    if m:
        result["audit_score"] = int(m.group(1))
        result["audit_max"] = int(m.group(2))

    # DEX [PAID]
    result["dex_paid"] = "DEX [PAID]" in text or "DEX[PAID]" in text

    # DEV: 3Lc...ALJG
    m = re.search(r'DEV:\s*([A-Za-z0-9]+(?:\.\.\.)?[A-Za-z0-9]*)', text)
    if m:
        result["dev_wallet"] = m.group(1)

    # Social links from TokenScan response
    socials = []
    websites = []
    
    # Find all URLs in response
    urls = re.findall(r'https?://[^\s<>"\']+', text)
    for url in urls:
        if 'twitter.com' in url or 'x.com' in url:
            socials.append({"url": url, "type": "twitter"})
        elif 't.me' in url:
            socials.append({"url": url, "type": "telegram"})
        elif 'dexscreener.com' in url or 'gmgn.ai' in url:
            pass  # Skip trading links
        else:
            websites.append({"url": url, "label": "Website"})
    
    # Also check for Website: pattern
    m = re.search(r'Website:\s*(https?://[^\s<>"\']+)', text, re.IGNORECASE)
    if m:
        url = m.group(1)
        if not any(w["url"] == url for w in websites):
            websites.append({"url": url, "label": "Website"})
    
    # Check for Twitter: pattern
    m = re.search(r'Twitter:\s*(https?://[^\s<>"\']+)', text, re.IGNORECASE)
    if m:
        url = m.group(1)
        if not any(s["url"] == url for s in socials):
            socials.append({"url": url, "type": "twitter"})
    
    result["socials"] = socials
    result["websites"] = websites
    result["has_website"] = len(websites) > 0
    result["has_twitter"] = any(s["type"] == "twitter" for s in socials)
    result["has_telegram"] = any(s["type"] == "telegram" for s in socials)

    return result


def _parse_number(s: str) -> float:
    """Parse formatted number like '34.36K' or '114.37K' or '1,234'."""
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


async def scan_token(client, address: str) -> dict:
    """
    Send token address to a TokenScan bot (rotated) and parse response.
    Returns structured data dict.
    """
    try:
        from telethon import errors

        bot_id = _next_bot()

        # Resolve entity first (required for new sessions)
        try:
            entity = await client.get_entity(bot_id)
        except Exception:
            # If can't resolve, try getting from dialogs
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.id == bot_id:
                        entity = dialog.entity
                        break
            except Exception:
                pass

        # Send address to bot
        await client.send_message(bot_id, address)

        # Wait for response
        await asyncio.sleep(3)

        # Get last 5 messages from this bot
        messages = await client.get_messages(bot_id, limit=5)

        for msg in messages:
            if msg.text and address[:8] in msg.text:
                logger.info(f"[TOKENSCAN-RESPONSE] bot={bot_id} ca={address[:12]} text={msg.text[:300]}")
                return _parse_tokenscan_response(msg.text)

        # If no matching message found, try once more with different bot
        await asyncio.sleep(3)
        retry_bot = _next_bot()
        messages = await client.get_messages(retry_bot, limit=3)
        for msg in messages:
            if msg.text and address[:8] in msg.text:
                logger.info(f"[TOKENSCAN-RESPONSE] bot={retry_bot} ca={address[:12]} text={msg.text[:300]}")
                return _parse_tokenscan_response(msg.text)

        logger.warning(f"TokenScan: no response for {address[:12]}...")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "bundled_pct": 0, "audit_score": 0}

    except errors.FloodWaitError as e:
        logger.warning(f"TokenScan flood wait: {e.seconds}s")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "bundled_pct": 0, "audit_score": 0}
    except Exception as e:
        logger.error(f"TokenScan error: {e}")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "bundled_pct": 0, "audit_score": 0}
