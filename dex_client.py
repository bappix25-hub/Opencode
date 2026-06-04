import asyncio
import aiohttp
import logging
from typing import Optional
from config import config

logger = logging.getLogger("dex_client")

class DexScreenerClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = "https://api.dexscreener.com"
        self.max_retries = config.dex_max_retries
        self.base_delay = config.dex_base_delay
    
    async def _request_with_retry(self, method: str, url: str, **kwargs) -> Optional[dict]:
        delay = self.base_delay
        for attempt in range(self.max_retries):
            try:
                async with self.session.request(method, url, **kwargs) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            wait_time = int(retry_after)
                        else:
                            wait_time = delay
                        logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{self.max_retries})")
                        await asyncio.sleep(wait_time)
                        delay *= 2
                        continue
                    else:
                        logger.error(f"DexScreener error {resp.status}: {url}")
                        return None
            except asyncio.TimeoutError:
                logger.warning(f"Timeout (attempt {attempt + 1}/{self.max_retries})")
                await asyncio.sleep(delay)
                delay *= 2
            except Exception as e:
                logger.error(f"Request error: {e}")
                await asyncio.sleep(delay)
                delay *= 2
        return None
    
    async def fetch_new_solana_pairs(self) -> list:
        url = f"{self.base_url}/token-profiles/latest/v1"
        data = await self._request_with_retry("GET", url)
        if data:
            return [p for p in data if p.get("chainId") == "solana"]
        return []
    
    async def fetch_boosted_pairs(self) -> list:
        url = f"{self.base_url}/token-boosts/latest/v1"
        data = await self._request_with_retry("GET", url)
        if data:
            return [p for p in data if p.get("chainId") == "solana"]
        return []
    
    async def fetch_pair_data(self, token_address: str) -> Optional[dict]:
        url = f"{self.base_url}/latest/dex/tokens/{token_address}"
        data = await self._request_with_retry("GET", url)
        if data:
            pairs = data.get("pairs", [])
            if pairs:
                pairs.sort(key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0), reverse=True)
                return pairs[0]
        return None