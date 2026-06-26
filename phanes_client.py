"""
phanes_client.py — Query Phanes bot for token analysis.

Phanes provides:
- MC, LP, Vol, Sup, 1H buy/sell, ATH
- Security: Fresh 1D/7D, Top 10%, TH, Dev Sold, DEX Paid
- Socials

Bot ID: 8436907499
"""

import asyncio
import logging
import re

logger = logging.getLogger("phanes_client")

PHANES_BOT_ID = 8436907499
TIMEOUT = 15


def _parse_phanes_response(text: str) -> dict:
    """Parse Phanes bot response into structured data."""
    result = {
        "holders": 0,
        "top10_pct": 0.0,
        "bundled_pct": 0.0,
        "mcp": 0.0,
        "liq_usd": 0.0,
        "volume_24h": 0.0,
        "fresh_1d": 0.0,
        "fresh_7d": 0.0,
        "dev_sold": False,
        "dex_paid": False,
        "parsed": True,
    }

    if not text:
        result["parsed"] = False
        return result

    # Strip markdown
    clean = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    clean = re.sub(r'`([^`]+)`', r'\1', clean)
    clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
    text = clean

    # MC
    m = re.search(r'MC\s*:?\s*\$?([0-9,.]+[KMB]?)', text, re.IGNORECASE)
    if m:
        val = m.group(1).replace(",", "")
        if val.endswith("B"): result["mcp"] = float(val[:-1]) * 1e9
        elif val.endswith("M"): result["mcp"] = float(val[:-1]) * 1e6
        elif val.endswith("K"): result["mcp"] = float(val[:-1]) * 1e3
        else: result["mcp"] = float(val) if val else 0

    # LP
    m = re.search(r'LP\s*:?\s*\$?([0-9,.]+[KMB]?)', text, re.IGNORECASE)
    if m:
        val = m.group(1).replace(",", "")
        if val.endswith("B"): result["liq_usd"] = float(val[:-1]) * 1e9
        elif val.endswith("M"): result["liq_usd"] = float(val[:-1]) * 1e6
        elif val.endswith("K"): result["liq_usd"] = float(val[:-1]) * 1e3
        else: result["liq_usd"] = float(val) if val else 0

    # Vol
    m = re.search(r'Vol\s*:?\s*\$?([0-9,.]+[KMB]?)', text, re.IGNORECASE)
    if m:
        val = m.group(1).replace(",", "")
        if val.endswith("B"): result["volume_24h"] = float(val[:-1]) * 1e9
        elif val.endswith("M"): result["volume_24h"] = float(val[:-1]) * 1e6
        elif val.endswith("K"): result["volume_24h"] = float(val[:-1]) * 1e3
        else: result["volume_24h"] = float(val) if val else 0

    # Holders
    m = re.search(r'HLD\s*:?\s*(\d+)', text)
    if m:
        result["holders"] = int(m.group(1))

    # Fresh 1D/7D
    m = re.search(r'Fresh\s*([0-9.]+)%\s*1D\s*\|\s*([0-9.]+)%\s*7D', text)
    if m:
        result["fresh_1d"] = float(m.group(1))
        result["fresh_7d"] = float(m.group(2))

    # Top 10
    m = re.search(r'Top\s*10\s*([0-9.]+)%', text)
    if m:
        try:
            val = float(m.group(1))
            if val <= 100:
                result["top10_pct"] = val
        except ValueError:
            pass

    # Dev Sold
    result["dev_sold"] = bool(re.search(r'Dev Sold\s*(🟢|✅)', text))

    # DEX Paid
    result["dex_paid"] = bool(re.search(r'DEX\s*Paid\s*(🟢|✅)', text))

    # Buy/Sell
    m = re.search(r'1H.*?B\s*([\d,]+).*?S\s*([\d,]+)', text)
    if m:
        buys = int(m.group(1).replace(",", ""))
        sells = int(m.group(2).replace(",", ""))
        result["buy_sell_ratio"] = buys / max(sells, 1)

    return result


async def scan_token(client, address: str) -> dict:
    """Send token address to Phanes bot and parse response."""
    try:
        from telethon import errors

        # Resolve entity first
        try:
            entity = await client.get_entity(PHANES_BOT_ID)
        except Exception:
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.id == PHANES_BOT_ID:
                        entity = dialog.entity
                        break
            except Exception:
                pass

        await client.send_message(PHANES_BOT_ID, address)
        await asyncio.sleep(4)

        messages = await client.get_messages(PHANES_BOT_ID, limit=5)
        for msg in messages:
            if msg.text and address[:8] in msg.text:
                logger.info(f"[PHANES-RESPONSE] ca={address[:12]} text={msg.text[:300]}")
                return _parse_phanes_response(msg.text)

        logger.warning(f"Phanes: no response for {address[:12]}...")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}

    except errors.FloodWaitError as e:
        logger.warning(f"Phanes flood wait: {e.seconds}s")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}
    except Exception as e:
        logger.error(f"Phanes error: {e}")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}