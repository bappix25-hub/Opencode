import aiohttp
import logging
from typing import Optional
from config import config

logger = logging.getLogger("helius_client")

class HeliusClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.api_key = config.helius_api_key
        self.base_url = "https://mainnet.helius-rpc.com"
    
    async def get_launch_transactions(self, address: str) -> list:
        try:
            url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={self.api_key}&limit=20&type=SWAP"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Helius tx error for {address}: {e}")
        return []
    
    async def get_holder_count(self, address: str) -> Optional[int]:
        try:
            url = f"{self.base_url}/?api-key={self.api_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccounts",
                "params": {"mint": address, "limit": 1}
            }
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", {}).get("total", 0)
        except Exception as e:
            logger.error(f"Helius holder error for {address}: {e}")
        return None