"""
fast_health.py — Parallel DexScreener + 3 Research Bots health check.

Races all sources, merges best data:
- DexScreener: price, volume, liquidity, fdv, price changes
- TokenScan: holders, top10%, bundled%, audit_score, dex_paid, dev_wallet
- Rick: holders, top10%, renounced, freeze_revoked, dev_status
- Phanes: fresh_1d/7d, top10%, dev_sold, dex_paid

Whichever responds first provides base data, then we merge from others.
"""

import asyncio
import logging
import time

logger = logging.getLogger("fast_health")

_cache = {}
_CACHE_TTL = 120  # 2 minutes


async def fast_check(dex_client, address: str, tg_client=None) -> dict:
    """
    Parallel health check: DexScreener + TokenScan + Rick + Phanes.
    Returns combined data dict.
    """
    now = time.time()
    cache_key = address
    if cache_key in _cache and now - _cache[cache_key]["time"] < _CACHE_TTL:
        return _cache[cache_key]["data"]

    result = {
        "healthy": True,
        "reason": "",
        "source": "none",
        "holders": 0,
        "top10_pct": 0,
        "bundled_pct": 0,
        "audit_score": 0,
        "dex_paid": False,
        "dev_wallet": "",
        "dev_status": "UNKNOWN",
        "renounced": False,
        "freeze_revoked": False,
        "fresh_1d": 0,
        "fresh_7d": 0,
        "price_usd": 0,
        "volume_24h": 0,
        "volume_5m": 0,
        "liquidity": 0,
        "fdv": 0,
        "price_change_5m": 0,
        "price_change_1h": 0,
        "txns_5m_buys": 0,
        "txns_5m_sells": 0,
        "pair_created": 0,
    }

    tasks = {}

    # DexScreener task
    async def dex_task():
        try:
            from dex_health import check_token_health
            return await check_token_health(dex_client, address)
        except Exception:
            return None

    tasks["dex"] = asyncio.create_task(dex_task())

    # TokenScan research bot task
    if tg_client:
        async def ts_task():
            try:
                from tokenscan_client import scan_token
                return await scan_token(tg_client, address)
            except Exception:
                return None

        tasks["ts"] = asyncio.create_task(ts_task())

        # Rick research bot task
        async def rick_task():
            try:
                from rick_client import scan_token
                return await scan_token(tg_client, address)
            except Exception:
                return None

        tasks["rick"] = asyncio.create_task(rick_task())

        # Phanes research bot task
        async def phanes_task():
            try:
                from phanes_client import scan_token
                return await scan_token(tg_client, address)
            except Exception:
                return None

        tasks["phanes"] = asyncio.create_task(phanes_task())

    # Wait for first result (DexScreener)
    done, pending = await asyncio.wait(
        list(tasks.values()),
        timeout=15,
        return_when=asyncio.FIRST_COMPLETED
    )

    # Process completed results
    for task_name, task in tasks.items():
        if task.done() and not task.cancelled():
            try:
                data = task.result()
                if data is None:
                    continue

                if task_name == "dex":
                    if data.get("data"):
                        dex_d = data["data"]
                        result["price_usd"] = dex_d.get("price_usd", 0)
                        result["volume_24h"] = dex_d.get("volume_24h", 0)
                        result["volume_5m"] = dex_d.get("volume_5m", 0)
                        result["liquidity"] = dex_d.get("liquidity", 0)
                        result["fdv"] = dex_d.get("fdv", 0)
                        result["price_change_5m"] = dex_d.get("price_change_5m", 0)
                        result["price_change_1h"] = dex_d.get("price_change_1h", 0)
                        result["txns_5m_buys"] = dex_d.get("txns_5m_buys", 0)
                        result["txns_5m_sells"] = dex_d.get("txns_5m_sells", 0)
                        result["pair_created"] = dex_d.get("pair_created", 0)
                    if not data.get("healthy"):
                        result["healthy"] = False
                        result["reason"] = data.get("reason", "")
                    if result["source"] == "none":
                        result["source"] = "dex"

                elif task_name == "ts" and data.get("parsed"):
                    result["holders"] = data.get("holders", 0) or result["holders"]
                    result["top10_pct"] = data.get("top10_pct", 0) or result["top10_pct"]
                    result["bundled_pct"] = data.get("bundled_pct", 0) or result["bundled_pct"]
                    result["audit_score"] = data.get("audit_score", 0) or result["audit_score"]
                    result["dex_paid"] = data.get("dex_paid", False) or result["dex_paid"]
                    result["dev_wallet"] = data.get("dev_wallet", "") or result["dev_wallet"]
                    if result["source"] == "none":
                        result["source"] = "tokenscan"

                elif task_name == "rick" and data.get("parsed"):
                    result["holders"] = data.get("holders", 0) or result["holders"]
                    result["top10_pct"] = data.get("top10_pct", 0) or result["top10_pct"]
                    result["renounced"] = data.get("renounced", False) or result["renounced"]
                    result["freeze_revoked"] = data.get("freeze_revoked", False) or result["freeze_revoked"]
                    result["dev_status"] = data.get("dev_status", "UNKNOWN") if data.get("dev_status", "UNKNOWN") != "UNKNOWN" else result["dev_status"]
                    if result["source"] == "none":
                        result["source"] = "rick"

                elif task_name == "phanes" and data.get("parsed"):
                    result["holders"] = data.get("holders", 0) or result["holders"]
                    result["top10_pct"] = data.get("top10_pct", 0) or result["top10_pct"]
                    result["fresh_1d"] = data.get("fresh_1d", 0) or result["fresh_1d"]
                    result["fresh_7d"] = data.get("fresh_7d", 0) or result["fresh_7d"]
                    result["dev_sold"] = data.get("dev_sold", False)
                    result["dex_paid"] = data.get("dex_paid", False) or result["dex_paid"]
                    if result["source"] == "none":
                        result["source"] = "phanes"

            except Exception:
                pass

    # Health checks from research bot data
    if result["holders"] > 0 and result["holders"] <= 5:
        result["healthy"] = False
        result["reason"] = f"Only {result['holders']} holders"
    if result["top10_pct"] > 50:
        result["healthy"] = False
        result["reason"] = f"Top10 {result['top10_pct']:.0f}% concentrated"
    if result["bundled_pct"] > 30:
        result["healthy"] = False
        result["reason"] = f"Bundled {result['bundled_pct']:.0f}%"
    if result["audit_score"] > 0 and result["audit_score"] < 4:
        result["healthy"] = False
        result["reason"] = f"Audit {result['audit_score']}/10"

    _cache[cache_key] = {"data": result, "time": now}
    return result