import aiohttp
import logging
from dataclasses import dataclass
from typing import Optional
from config import config

logger = logging.getLogger("rugcheck_client")

RISK_CHECKS = [
    "Freeze Authority still enabled",
    "Mint Authority still enabled",
    "Honeypot",
    "High tax",
    "Low liquidity",
    "Single holder",
    "Mutable metadata"
]

@dataclass
class RiskReport:
    score: int
    risks: list[str]
    lp_locked: float
    is_risky: bool
    risk_details: list[dict]

class RugcheckClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = config.rugcheck_url
    
    async def check_token(self, address: str, symbol: str = "?") -> Optional[RiskReport]:
        try:
            url = f"{self.base_url}/tokens/{address}/report/summary"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_report(data, symbol)
        except Exception as e:
            logger.error(f"Rugcheck error for {symbol}: {e}")
        return None
    
    def _parse_report(self, data: dict, symbol: str) -> RiskReport:
        score = data.get("score", 0)
        risks = data.get("risks", [])
        lp_locked = data.get("lpLockedPct", 0)
        
        risk_names = [r.get("name", "") for r in risks if isinstance(r, dict)]
        
        critical_risks = [
            "Freeze Authority still enabled",
            "Mint Authority still enabled",
            "Honeypot"
        ]
        
        is_risky = any(r in critical_risks for r in risk_names)
        
        high_tax = any("tax" in r.lower() and ("high" in r.lower() or "100" in r) for r in risk_names)
        if high_tax:
            is_risky = True
        
        logger.info(f"RUGCHECK {symbol} | score={score} | risks={risk_names[:5]} | lp_locked={lp_locked}% | risky={is_risky}")
        
        return RiskReport(
            score=score,
            risks=risk_names,
            lp_locked=lp_locked,
            is_risky=is_risky,
            risk_details=risks
        )