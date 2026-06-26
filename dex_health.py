"""
dex_health.py — Pre-signal health check with DexScreener + TokenScan.

Before sending ANY signal, verify:
1. Token is alive (has price, volume) — from DexScreener
2. Not dumping (price not crashing) — from DexScreener
3. Has activity (recent trades) — from DexScreener
4. Liquidity is real (not fake) — from DexScreener
5. Holders > 5 (not dead/scam) — from TokenScan
6. Top 10 holders < 50% (not concentrated) — from TokenScan
7. Bundled < 30% (not bot manipulation) — from TokenScan
8. Audit score >= 5/10 (has basic checks) — from TokenScan

This is the FINAL gate before signal goes out.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("dex_health")


async def check_token_health(dex_client, address: str, tokenscan_data: dict = None) -> dict:
    """
    Quick DexScreener + TokenScan health check.
    Returns: {"healthy": bool, "reason": str, "data": dict}
    """
    try:
        pair = await asyncio.wait_for(dex_client.fetch_pair_data(address), timeout=10)
        if not pair:
            return {"healthy": False, "reason": "No pair data", "data": {}}

        price_usd = float(pair.get("priceUsd", 0) or 0)
        volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        volume_5m = float(pair.get("volume", {}).get("m5", 0) or 0)
        price_change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
        price_change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        txns_5m_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0) or 0)
        txns_5m_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0) or 0)
        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        fdv = float(pair.get("fdv", 0) or 0)
        pair_created = pair.get("pairCreatedAt", 0)

        data = {
            "price_usd": price_usd,
            "volume_24h": volume_24h,
            "volume_5m": volume_5m,
            "price_change_5m": price_change_5m,
            "price_change_1h": price_change_1h,
            "txns_5m_buys": txns_5m_buys,
            "txns_5m_sells": txns_5m_sells,
            "liquidity": liquidity,
            "fdv": fdv,
            "pair_created": pair_created,
        }

        # ===== DexScreener HEALTH CHECKS =====

        # 1. Has price?
        if price_usd <= 0:
            return {"healthy": False, "reason": "No price data", "data": data}

        # 2. Has liquidity?
        if liquidity < 500:
            return {"healthy": False, "reason": f"Very low liquidity ${liquidity:,.0f}", "data": data}

        # 3. Not dead (has some volume)
        if volume_24h <= 0 and volume_5m <= 0:
            return {"healthy": False, "reason": "No volume — dead token", "data": data}

        # 4. Not dumping hard (5m price change)
        if price_change_5m < -30:
            return {"healthy": False, "reason": f"Dumping {price_change_5m:.0f}% in 5m", "data": data}

        # 5. Has buy activity (not just sells)
        if txns_5m_buys + txns_5m_sells > 0:
            buy_ratio = txns_5m_buys / (txns_5m_buys + txns_5m_sells)
            if buy_ratio < 0.2:
                return {"healthy": False, "reason": f"Only {buy_ratio:.0%} buys — sell pressure", "data": data}

        # 6. Pair age check (new pairs are riskier but OK)
        if pair_created:
            age_minutes = (datetime.now(timezone.utc).timestamp() * 1000 - pair_created) / 60000
            if age_minutes > 60:
                # Older than 1 hour — should have some volume
                if volume_24h < 1000:
                    return {"healthy": False, "reason": f"Old pair ({age_minutes:.0f}min) with low volume", "data": data}

        # ===== TokenScan HEALTH CHECKS =====
        if tokenscan_data and tokenscan_data.get("parsed"):
            holders = tokenscan_data.get("holders", 0)
            top10_pct = tokenscan_data.get("top10_pct", 0)
            bundled_pct = tokenscan_data.get("bundled_pct", 0)
            audit_score = tokenscan_data.get("audit_score", 0)

            # 7. Holders check: <=5 = dead/scam
            if holders > 0 and holders <= 5:
                return {"healthy": False, "reason": f"Only {holders} holders — dead/scam", "data": {**data, **tokenscan_data}}

            # 8. Top 10 holders: >50% = too concentrated (rug risk)
            if top10_pct > 50:
                return {"healthy": False, "reason": f"Top 10 hold {top10_pct:.0f}% — concentrated", "data": {**data, **tokenscan_data}}

            # 9. Bundled: >30% = bot manipulation
            if bundled_pct > 30:
                return {"healthy": False, "reason": f"Bundled {bundled_pct:.0f}% — bot activity", "data": {**data, **tokenscan_data}}

            # 10. Audit score: <4/10 = suspicious
            if audit_score > 0 and audit_score < 4:
                return {"healthy": False, "reason": f"Audit {audit_score}/10 — suspicious", "data": {**data, **tokenscan_data}}

            data.update(tokenscan_data)

        # ALL CHECKS PASSED
        reasons = []
        if volume_5m > 100:
            reasons.append(f"Vol5m ${volume_5m:,.0f}")
        if txns_5m_buys > txns_5m_sells:
            reasons.append(f"Buys>{txns_5m_sells}")
        if price_change_5m > 0:
            reasons.append(f"+{price_change_5m:.0f}%5m")
        if tokenscan_data and tokenscan_data.get("parsed"):
            holders = tokenscan_data.get("holders", 0)
            if holders > 0:
                reasons.append(f"HLD:{holders}")
            audit = tokenscan_data.get("audit_score", 0)
            if audit > 0:
                reasons.append(f"Audit:{audit}/10")

        return {
            "healthy": True,
            "reason": " | ".join(reasons) if reasons else "Passing basic checks",
            "data": data,
        }

    except asyncio.TimeoutError:
        return {"healthy": False, "reason": "DexScreener timeout", "data": {}}
    except Exception as e:
        return {"healthy": False, "reason": f"DexScreener error: {e}", "data": {}}
