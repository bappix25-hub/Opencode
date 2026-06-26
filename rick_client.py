"""
rick_client.py — Query Rick bot for token analysis.

Rick provides:
- Market Cap
- Liquidity
- Holders
- Top holders %
- Renounced
- Freeze Revoked
- Dev Wallet Empty
- Wallet Has Decent History
- Liquid Supply %
- Pooled SOL
- Total Supply

Bot ID: 6126376117
"""

import asyncio
import logging
import re

logger = logging.getLogger("rick_client")

RICK_BOT_ID = 6126376117
TIMEOUT = 15


def _parse_rick_response(text: str) -> dict:
    """Parse Rick bot response into structured data.
    
    New Rick format (2026+):
    [🟡] [**TOKEN**] **[44.2M/1%]** **$TOKEN**
    💎 FDV: `44.2M` ⇨ `2B` `[20mo]`
    💦 Liq: `1.4M` `[x32]` ⋅ ‼️ `0%`
    📊 Vol: `617K` ⋅ Age: `2y`
    [👥] TH: [12.0]...[38%]
    🌱 Fresh 1D: `1%` ⋅ 7D: `1%`
    """
    result = {
        "holders": 0,
        "top10_pct": 0.0,
        "mcp": 0.0,
        "liq_usd": 0.0,
        "volume_24h": 0.0,
        "renounced": False,
        "freeze_revoked": False,
        "dev_wallet_empty": False,
        "wallet_decent_history": False,
        "dev_status": "UNKNOWN",
        "fresh_1d": 0.0,
        "fresh_7d": 0.0,
        "parsed": True,
    }

    if not text:
        result["parsed"] = False
        return result

    def _parse_val(val_str):
        val_str = val_str.replace(",", "").strip("`").strip()
        if val_str.endswith("B"): return float(val_str[:-1]) * 1e9
        elif val_str.endswith("M"): return float(val_str[:-1]) * 1e6
        elif val_str.endswith("K"): return float(val_str[:-1]) * 1e3
        else:
            try: return float(val_str)
            except: return 0

    # FDV / Market Cap: `44.2M`
    m = re.search(r'FDV:\s*`([0-9,.]+[KMB]?)`', text)
    if m:
        result["mcp"] = _parse_val(m.group(1))
    else:
        # Fallback: look for MC pattern like [44.2M/1%]
        m = re.search(r'\[([0-9,.]+[KMB]?)/', text)
        if m:
            result["mcp"] = _parse_val(m.group(1))

    # Liquidity: `1.4M`
    m = re.search(r'Liq:\s*`([0-9,.]+[KMB]?)`', text)
    if m:
        result["liq_usd"] = _parse_val(m.group(1))

    # Volume: `617K`
    m = re.search(r'Vol:\s*`([0-9,.]+[KMB]?)`', text)
    if m:
        result["volume_24h"] = _parse_val(m.group(1))

    # Top holders %: TH: ...[38%]
    m = re.search(r'TH:.*?\[([0-9.]+)%\]', text)
    if m:
        try:
            val = float(m.group(1))
            if val <= 100:
                result["top10_pct"] = val
        except ValueError:
            pass

    # Fresh 1D and 7D
    m = re.search(r'Fresh 1D:\s*`([0-9.]+)%`', text)
    if m:
        result["fresh_1d"] = float(m.group(1))
    m = re.search(r'7D:\s*`([0-9.]+)%`', text)
    if m:
        result["fresh_7d"] = float(m.group(1))

    # Holders from TH total: Total: `136K`
    m = re.search(r'Total:\s*`([0-9,.]+[KMB]?)`', text)
    if m:
        result["holders"] = int(_parse_val(m.group(1)))

    return result


async def scan_token(client, address: str) -> dict:
    """
    Send token address to Rick bot and parse response.
    Returns structured data dict.
    """
    try:
        from telethon import errors

        # Resolve entity first
        try:
            entity = await client.get_entity(RICK_BOT_ID)
        except Exception:
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.id == RICK_BOT_ID:
                        entity = dialog.entity
                        break
            except Exception:
                pass

        # Send address to bot
        await client.send_message(RICK_BOT_ID, address)

        # Wait for response
        await asyncio.sleep(4)

        # Get last 5 messages from this bot
        messages = await client.get_messages(RICK_BOT_ID, limit=5)

        for msg in messages:
            if msg.text and address[:8] in msg.text:
                logger.info(f"[RICK-RESPONSE] ca={address[:12]} text={msg.text[:300]}")
                return _parse_rick_response(msg.text)

        logger.warning(f"Rick: no response for {address[:12]}...")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}

    except errors.FloodWaitError as e:
        logger.warning(f"Rick flood wait: {e.seconds}s")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}
    except Exception as e:
        logger.error(f"Rick error: {e}")
        return {"parsed": False, "holders": 0, "top10_pct": 0, "mcp": 0, "liq_usd": 0}