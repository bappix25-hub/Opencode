"""
gmgn_trending.py — Scan GMGN trending/new tokens for early detection.

Instead of waiting for signals, PROACTIVELY scan:
1. GMGN trending tokens (high volume, active community)
2. GMGN new pairs (fresh launches)
3. Tokens with growing holders over time
4. Tokens making small pumps (accumulation phase)

This catches tokens BEFORE they get big signals.
"""

import asyncio
import json
import os
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger("meme_bot.gmgn_trending")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
TRENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmgn_trending.json")


def _load_trending():
    try:
        if os.path.exists(TRENDING_FILE):
            with open(TRENDING_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"tracked": {}, "scans": []}


def _save_trending(data):
    try:
        with open(TRENDING_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save trending: {e}")


async def scan_gmgn_trending(dex_client):
    """
    Scan GMGN trending page for potential early gems.
    Uses DexScreener to get trending tokens data.
    """
    trending_data = _load_trending()
    tracked = trending_data.setdefault("tracked", {})

    try:
        # Use DexScreener to get trending tokens
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Get trending tokens from DexScreener
            url = "https://api.dexscreener.com/token-boosts/top/v1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener trending API returned {resp.status}")
                    return []
                tokens = await resp.json()

        now = datetime.now(timezone.utc).timestamp()
        new_tokens = []

        for token_data in tokens[:20]:  # Top 20 trending
            chain = token_data.get("chainId", "")
            if chain != "solana":
                continue

            address = token_data.get("tokenAddress", "")
            if not address:
                continue

            if address in tracked:
                # Update existing tracked token with current data
                try:
                    pair = await asyncio.wait_for(dex_client.fetch_pair_data(address), timeout=10)
                    if pair:
                        old = tracked[address]
                        liquidity = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
                        fdv = float(pair.get("fdv", 0) or 0)
                        volume_24h = float((pair.get("volume") or {}).get("h24", 0) or 0)
                        
                        # Track how long token has been active
                        first_seen = old.get("first_seen_new", now)
                        hours_active = (now - first_seen) / 3600
                        
                        old["liq_usd"] = liquidity
                        old["mc"] = fdv
                        old["volume_24h"] = volume_24h
                        old["hours_active"] = round(hours_active, 1)
                        old["last_updated"] = now
                        
                        # Mark as long-time active if >6h with LP > $5000
                        if hours_active > 6 and liquidity > 5000:
                            old["long_time_active"] = True
                            logger.info(
                                f"[LONG-TIME] {old['symbol']} active {hours_active:.1f}h "
                                f"MC=${fdv:,.0f} Liq=${liquidity:,.0f}"
                            )
                except Exception:
                    pass
                continue

            # Get detailed pair data from DexScreener
            try:
                pair = await asyncio.wait_for(dex_client.fetch_pair_data(address), timeout=10)
                if not pair:
                    continue

                price_usd = float(pair.get("priceUsd", 0) or 0)
                volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
                volume_5m = float(pair.get("volume", {}).get("m5", 0) or 0)
                price_change_24h = float(pair.get("priceChange", {}).get("h24", 0) or 0)
                price_change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                fdv = float(pair.get("fdv", 0) or 0)
                txns_24h_buys = int(pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0)
                txns_24h_sells = int(pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
                pair_created = pair.get("pairCreatedAt", 0)
                base_token = pair.get("baseToken") or {}
                symbol = base_token.get("symbol", "???")
                name = base_token.get("name", "???")

                # Calculate age in hours
                if pair_created:
                    age_hours = (now * 1000 - pair_created) / 3600000
                else:
                    age_hours = 0

                # Buy/sell ratio
                total_txns = txns_24h_buys + txns_24h_sells
                buy_ratio = txns_24h_buys / max(total_txns, 1)

                token_info = {
                    "symbol": symbol,
                    "name": name,
                    "ca": address,
                    "price_usd": price_usd,
                    "mc": fdv,
                    "liq_usd": liquidity,
                    "volume_24h": volume_24h,
                    "volume_5m": volume_5m,
                    "price_change_24h": price_change_24h,
                    "price_change_1h": price_change_1h,
                    "buy_ratio": buy_ratio,
                    "txns_24h": total_txns,
                    "age_hours": age_hours,
                    "first_seen_trending": now,
                }

                tracked[address] = token_info
                new_tokens.append(token_info)

                logger.info(
                    f"[TRENDING] {symbol} MC=${fdv:,.0f} Liq=${liquidity:,.0f} "
                    f"Vol24h=${volume_24h:,.0f} Age={age_hours:.1f}h "
                    f"BuyRatio={buy_ratio:.0%} PC24h={price_change_24h:+.0f}%"
                )

            except Exception as e:
                logger.debug(f"Failed to get pair data for {address[:12]}: {e}")
                continue

            # Rate limit
            await asyncio.sleep(0.5)

        # Save
        scan_record = {
            "timestamp": now,
            "tokens_found": len(new_tokens),
            "total_tracked": len(tracked),
        }
        trending_data.setdefault("scans", []).append(scan_record)
        if len(trending_data["scans"]) > 100:
            trending_data["scans"] = trending_data["scans"][-100:]

        _save_trending(trending_data)

        if new_tokens:
            logger.info(f"[TRENDING] Found {len(new_tokens)} new trending tokens")

        return new_tokens

    except Exception as e:
        logger.error(f"Trending scan error: {e}")
        return []


async def scan_gmgn_new_pairs(dex_client):
    """
    Scan for new token launches on DexScreener.
    These are the earliest opportunities.
    """
    trending_data = _load_trending()
    tracked = trending_data.setdefault("tracked", {})

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # DexScreener new pairs for Solana
            url = "https://api.dexscreener.com/latest/dex/tokens/solana"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        now = datetime.now(timezone.utc).timestamp()
        new_tokens = []

        pairs = data.get("pairs") or []  # API may return null
        pairs = pairs[:30]  # Latest 30 pairs

        for pair in pairs:
            if not pair:
                continue
            base_token = pair.get("baseToken") or {}
            address = base_token.get("address", "")
            if not address or address in tracked:
                continue

            price_usd = float(pair.get("priceUsd", 0) or 0)
            fdv = float(pair.get("fdv", 0) or 0)
            liquidity = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
            volume_5m = float((pair.get("volume") or {}).get("m5", 0) or 0)
            price_change_5m = float((pair.get("priceChange") or {}).get("m5", 0) or 0)
            pair_created = pair.get("pairCreatedAt", 0)
            symbol = base_token.get("symbol", "???")
            name = base_token.get("name", "???")

            if pair_created:
                age_minutes = (now * 1000 - pair_created) / 60000
            else:
                age_minutes = 999

            # Only very new tokens (< 1 hour)
            if age_minutes > 60:
                continue

            txns_5m_buys = int(((pair.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
            txns_5m_sells = int(((pair.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
            total_5m = txns_5m_buys + txns_5m_sells
            buy_ratio = txns_5m_buys / max(total_5m, 1)

            token_info = {
                "symbol": symbol,
                "name": name,
                "ca": address,
                "price_usd": price_usd,
                "mc": fdv,
                "liq_usd": liquidity,
                "volume_5m": volume_5m,
                "price_change_5m": price_change_5m,
                "buy_ratio": buy_ratio,
                "age_minutes": age_minutes,
                "first_seen_new": now,
                "source": "new_pair",
            }

            tracked[address] = token_info
            new_tokens.append(token_info)

            logger.info(
                f"[NEW PAIR] {symbol} MC=${fdv:,.0f} Liq=${liquidity:,.0f} "
                f"Vol5m=${volume_5m:,.0f} Age={age_minutes:.0f}min "
                f"BuyRatio={buy_ratio:.0%} PC5m={price_change_5m:+.0f}%"
            )

        _save_trending(trending_data)

        if new_tokens:
            logger.info(f"[NEW PAIRS] Found {len(new_tokens)} new token launches")

        return new_tokens

    except Exception as e:
        logger.error(f"New pairs scan error: {e}")
        return []


def score_trending_token(token_info: dict) -> dict:
    """
    Score a trending token for early detection potential.
    Higher score = more likely to pump.
    """
    score = 0
    reasons = []

    mc = token_info.get("mc", 0)
    liq = token_info.get("liq_usd", 0)
    volume_24h = token_info.get("volume_24h", 0)
    volume_5m = token_info.get("volume_5m", 0)
    price_change_24h = token_info.get("price_change_24h", 0)
    price_change_1h = token_info.get("price_change_1h", 0)
    buy_ratio = token_info.get("buy_ratio", 0.5)
    age_hours = token_info.get("age_hours", 0)
    txns = token_info.get("txns_24h", 0)

    # 1. Early stage (low MC = high potential)
    if mc < 10000:
        score += 25
        reasons.append(f"Very early MC ${mc:,.0f}")
    elif mc < 50000:
        score += 20
        reasons.append(f"Early MC ${mc:,.0f}")
    elif mc < 200000:
        score += 10
        reasons.append(f"Mid MC ${mc:,.0f}")

    # 2. Buy pressure (more buys than sells = accumulation)
    if buy_ratio > 0.65:
        score += 20
        reasons.append(f"Strong buy pressure {buy_ratio:.0%}")
    elif buy_ratio > 0.55:
        score += 10
        reasons.append(f"Buy pressure {buy_ratio:.0%}")

    # 3. Volume activity
    if volume_5m > 5000:
        score += 15
        reasons.append(f"Volume spike ${volume_5m:,.0f}")
    elif volume_24h > 50000:
        score += 10
        reasons.append(f"Good 24h volume ${volume_24h:,.0f}")

    # 4. Liquidity (enough to trade)
    if liq > 5000:
        score += 10
        reasons.append(f"Good liquidity ${liq:,.0f}")
    elif liq > 1000:
        score += 5
        reasons.append(f"Adequate liquidity ${liq:,.0f}")

    # 5. Price momentum (positive but not too much)
    if 10 < price_change_24h < 100:
        score += 10
        reasons.append(f"Good momentum +{price_change_24h:.0f}%")
    elif price_change_24h > 100:
        score += 5
        reasons.append(f"High momentum +{price_change_24h:.0f}%")

    # 6. Age (sweet spot: 1-24 hours)
    if 1 < age_hours < 24:
        score += 10
        reasons.append(f"Sweet spot age {age_hours:.1f}h")

    # 7. Activity (has trading activity)
    if txns > 100:
        score += 5
        reasons.append(f"Active ({txns} txns)")

    score = min(score, 100)

    return {
        "score": score,
        "reasons": reasons,
        "verdict": "EARLY_GEM" if score >= 60 else "WATCH" if score >= 40 else "SKIP",
    }


def get_trending_analysis():
    """Get analysis of all tracked trending tokens."""
    trending_data = _load_trending()
    tracked = trending_data.get("tracked", {})

    scored = []
    for ca, info in tracked.items():
        result = score_trending_token(info)
        scored.append({
            "ca": ca,
            "symbol": info.get("symbol", "?"),
            "mc": info.get("mc", 0),
            "liq": info.get("liq_usd", 0),
            "volume_24h": info.get("volume_24h", 0),
            "age_hours": info.get("age_hours", 0),
            "score": result["score"],
            "verdict": result["verdict"],
            "reasons": result["reasons"],
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    return {
        "total_tracked": len(tracked),
        "top_tokens": scored[:20],
        "scans": trending_data.get("scans", [])[-5:],
    }
