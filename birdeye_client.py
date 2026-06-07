import aiohttp
import logging
from typing import Optional

logger = logging.getLogger("birdeye_client")

BIRDEYE_BASE = "https://public-api.birdeye.so"

SOL_MINT = "So11111111111111111111111111111111111111112"


class BirdeyeClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        self.session = session
        self.api_key = api_key
        self.enabled = bool(api_key)
        if self.enabled:
            logger.info("✅ Birdeye API enabled")
        else:
            logger.info("ℹ️ Birdeye API disabled (no API key)")

    def _headers(self) -> dict:
        h = {"x-chain": "solana"}
        if self.api_key:
            h["X-API-KEY"] = self.api_key
        return h

    async def get_token_overview(self, address: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            url = f"{BIRDEYE_BASE}/defi/token_overview"
            params = {"address": address}
            async with self.session.get(
                url, params=params, headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("data", {})
                    if result:
                        return {
                            "price": float(result.get("price", 0) or 0),
                            "volume_24h": float(result.get("volume24h", 0) or 0),
                            "volume_change_24h": float(result.get("volumeChange24h", 0) or 0),
                            "liquidity": float(result.get("liquidity", 0) or 0),
                            "holder": int(result.get("holder", 0) or 0),
                            "trade_24h": int(result.get("trade24h", 0) or 0),
                            "buy_24h": int(result.get("buy24h", 0) or 0),
                            "sell_24h": int(result.get("sell24h", 0) or 0),
                            "price_change_24h": float(result.get("priceChange24h", 0) or 0),
                            "mc": float(result.get("mc", 0) or 0),
                            "nft_dusted_24h": int(result.get("nftDusted24h", 0) or 0),
                            "unique_wallets_24h": int(result.get("uniqueWallet24h", 0) or 0),
                        }
                elif resp.status == 429:
                    logger.warning("Birdeye rate limited")
        except Exception as e:
            logger.debug(f"Birdeye token_overview error for {address}: {e}")
        return None

    async def get_top_holders(self, address: str, limit: int = 20) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            url = f"{BIRDEYE_BASE}/defi/token_holders"
            params = {"address": address, "limit": min(limit, 50)}
            async with self.session.get(
                url, params=params, headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    if items:
                        total_supply = sum(float(h.get("amount", 0) or 0) for h in items)
                        top10_pct = 0.0
                        if total_supply > 0:
                            top10_amount = sum(float(h.get("amount", 0) or 0) for h in items[:10])
                            top10_pct = (top10_amount / total_supply) * 100
                        return {
                            "holder_count": len(items),
                            "top10_holder_pct": round(top10_pct, 1),
                            "holders": items[:limit],
                        }
        except Exception as e:
            logger.debug(f"Birdeye top_holders error for {address}: {e}")
        return None

    async def get_ohlcv(self, address: str, interval: str = "1h", limit: int = 24) -> Optional[list]:
        if not self.enabled:
            return None
        try:
            url = f"{BIRDEYE_BASE}/defi/ohlcv"
            params = {
                "address": address,
                "type": interval,
                "limit": min(limit, 100),
            }
            async with self.session.get(
                url, params=params, headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    if items:
                        return [
                            {
                                "open": float(c.get("open", 0)),
                                "high": float(c.get("high", 0)),
                                "low": float(c.get("low", 0)),
                                "close": float(c.get("close", 0)),
                                "volume": float(c.get("volume", 0)),
                                "time": c.get("unix_time", 0),
                            }
                            for c in items
                        ]
        except Exception as e:
            logger.debug(f"Birdeye OHLCV error for {address}: {e}")
        return None

    async def get_token_security(self, address: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            url = f"{BIRDEYE_BASE}/defi/token_security"
            params = {"address": address}
            async with self.session.get(
                url, params=params, headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("data", {})
                    if result:
                        return {
                            "is_open_source": result.get("isOpenSource", False),
                            "is_mutable_metadata": result.get("isMutableMetadata", False),
                            "freeze_authority": result.get("freezeAuthority"),
                            "mint_authority": result.get("mintAuthority"),
                            "top_holders": result.get("topHolders", []),
                        }
        except Exception as e:
            logger.debug(f"Birdeye security error for {address}: {e}")
        return None
