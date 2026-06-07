import aiohttp
import logging
from typing import Optional

logger = logging.getLogger("jupiter_client")

JUPITER_QUOTE = "https://api.jup.ag/swap/v2/quote"
JUPITER_PRICE = "https://api.jup.ag/price/v2"
JUPITER_PRICE_ID = "https://api.jup.ag/price"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class JupiterClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        logger.info("✅ Jupiter price feed enabled")

    async def get_token_price(self, address: str) -> Optional[float]:
        try:
            url = f"{JUPITER_PRICE}?ids={address}"
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price_data = data.get("data", {}).get(address, {})
                    price = float(price_data.get("price", 0) or 0)
                    if price > 0:
                        return price
        except Exception as e:
            logger.debug(f"Jupiter price error for {address}: {e}")
        return None

    async def get_quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 500) -> Optional[dict]:
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
            }
            async with self.session.get(
                JUPITER_QUOTE, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data.get("outAmount"):
                        in_amount = int(data.get("inAmount", 0) or 0)
                        out_amount = int(data.get("outAmount", 0) or 0)
                        price_impact_pct = float(data.get("priceImpactPct", 0) or 0)
                        return {
                            "in_amount": in_amount,
                            "out_amount": out_amount,
                            "price_impact_pct": price_impact_pct,
                            "route": data.get("routePlan", []),
                            "market_infos": data.get("marketInfos", []),
                        }
        except Exception as e:
            logger.debug(f"Jupiter quote error: {e}")
        return None

    async def verify_price(self, address: str, dexscreener_price: float) -> Optional[dict]:
        jupiter_price = await self.get_token_price(address)
        if jupiter_price is None or dexscreener_price <= 0:
            return None

        diff_pct = abs(jupiter_price - dexscreener_price) / max(dexscreener_price, 0.000000001) * 100
        is_manipulated = diff_pct > 10

        return {
            "jupiter_price": jupiter_price,
            "dexscreener_price": dexscreener_price,
            "diff_pct": round(diff_pct, 2),
            "is_manipulated": is_manipulated,
            "verified": not is_manipulated,
        }

    async def get_price_impact(self, token_address: str, sol_amount: float = 0.01) -> Optional[dict]:
        lamports = int(sol_amount * 1e9)
        quote = await self.get_quote(SOL_MINT, token_address, lamports)
        if not quote:
            return None

        impact = quote["price_impact_pct"]
        return {
            "price_impact_pct": impact,
            "safe": abs(impact) < 5,
            "warning": abs(impact) > 10,
            "out_amount": quote["out_amount"],
        }
