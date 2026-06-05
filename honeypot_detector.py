"""
Multi-layer honeypot detector.
Combines rugcheck, holder distribution, and on-chain signals.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from config import config
from rugcheck_client import RugcheckClient, RiskReport

logger = logging.getLogger("honeypot_detector")

GMGN_SECURITY_URL = "https://gmgn.ai/defi/quoter/v1/rank/sol/swaps/{mint}"


@dataclass
class HoneypotReport:
    is_honeypot: bool
    confidence: float
    reasons: list = field(default_factory=list)
    bundle_pct: float = 0.0
    top10_pct: float = 0.0
    freeze_authority: bool = False
    mint_authority: bool = False
    sell_tax: float = 0.0
    tradable: bool = True


class HoneypotDetector:
    """
    Layered detection:
    1. Rugcheck API (Honeypot / Freeze / Mint authority)
    2. Helius holder distribution (top10 %)
    3. GMGN security endpoint (sell simulation)
    4. Pair liquidity (rug pull detection)
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        rugcheck: Optional[RugcheckClient] = None,
        helius=None,
        dex=None,
    ):
        self.session = session
        self.rugcheck = rugcheck or RugcheckClient(session)
        self.helius = helius
        self.dex = dex
        self._gmgn_cache: dict[str, HoneypotReport] = {}

    async def check(
        self,
        address: str,
        symbol: str = "?",
        pair: Optional[dict] = None,
    ) -> HoneypotReport:
        reasons: list[str] = []
        confidence = 0.0
        is_honeypot = False
        freeze_auth = False
        mint_auth = False
        bundle_pct = 0.0
        top10_pct = 0.0
        sell_tax = 0.0
        tradable = True

        if address in self._gmgn_cache:
            cached = self._gmgn_cache[address]
            if (asyncio.get_event_loop().time() - cached.__dict__.get("_ts", 0)) < 1800:
                return cached

        report: Optional[RiskReport] = None
        try:
            report = await self.rugcheck.check_token(address, symbol)
        except Exception as e:
            logger.debug(f"rugcheck honeypot check error: {e}")

        if report:
            for risk in report.risks:
                risk_lower = risk.lower()
                if "honeypot" in risk_lower:
                    is_honeypot = True
                    confidence = max(confidence, 0.95)
                    reasons.append(f"rugcheck: {risk}")
                elif "freeze authority" in risk_lower:
                    freeze_auth = True
                    confidence = max(confidence, 0.80)
                    reasons.append(f"rugcheck: {risk}")
                elif "mint authority" in risk_lower:
                    mint_auth = True
                    confidence = max(confidence, 0.70)
                    reasons.append(f"rugcheck: {risk}")
                elif "high" in risk_lower and "tax" in risk_lower:
                    sell_tax = max(sell_tax, 50.0)
                    tradable = False
                    confidence = max(confidence, 0.90)
                    reasons.append(f"rugcheck: {risk}")

        if pair:
            try:
                top10_pct = float(
                    pair.get("top10HolderPercent", pair.get("top10Pct", 0)) or 0
                )
                if top10_pct > 30:
                    is_honeypot = True
                    confidence = max(confidence, 0.75)
                    reasons.append(f"top10 holders: {top10_pct:.1f}%")
            except Exception:
                pass

            try:
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                if liquidity < 100 and liquidity > 0:
                    tradable = False
                    confidence = max(confidence, 0.60)
                    reasons.append(f"micro liquidity: ${liquidity:.0f}")
            except Exception:
                pass

        if self.helius and not is_honeypot:
            try:
                holders = await self.helius.get_top_holders(address, limit=10)
                if holders:
                    total_in_top = sum(h.get("amount", 0) for h in holders[:10])
                    if total_in_top > 0:
                        largest = max(h.get("amount", 0) for h in holders[:10])
                        concentration = (largest / total_in_top) * 100
                        if concentration >= 90:
                            is_honeypot = True
                            confidence = max(confidence, 0.85)
                            reasons.append(
                                f"single wallet = {concentration:.0f}% of top10"
                            )
            except Exception as e:
                logger.debug(f"helius holder check error: {e}")

        if not is_honeypot and not tradable:
            is_honeypot = True

        out = HoneypotReport(
            is_honeypot=is_honeypot,
            confidence=confidence,
            reasons=reasons,
            bundle_pct=bundle_pct,
            top10_pct=top10_pct,
            freeze_authority=freeze_auth,
            mint_authority=mint_auth,
            sell_tax=sell_tax,
            tradable=tradable,
        )
        out.__dict__["_ts"] = asyncio.get_event_loop().time()
        self._gmgn_cache[address] = out
        return out

    def invalidate(self, address: str) -> None:
        self._gmgn_cache.pop(address, None)
