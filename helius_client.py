import aiohttp
import logging
from typing import Optional
from config import config

logger = logging.getLogger("helius_client")

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"


class HeliusClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.api_key = config.helius_api_key
        self.base_url = "https://mainnet.helius-rpc.com"
        self.das_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

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
            payload = {
                "jsonrpc": "2.0",
                "id": "helius-holder",
                "method": "getTokenAccounts",
                "params": {"mint": address, "limit": 1000, "options": {"showZeroBalance": False}},
            }
            async with self.session.post(
                self.das_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = (
                        data.get("result", {}).get("token_accounts", [])
                        or data.get("result", {}).get("accounts", [])
                        or []
                    )
                    if accounts:
                        return len(accounts)
        except Exception as e:
            logger.debug(f"Helius DAS getTokenAccounts error for {address}: {e}")
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "helius-largest",
                "method": "getTokenLargestAccounts",
                "params": [address],
            }
            async with self.session.post(
                self.base_url, json=payload, params={"api-key": self.api_key},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    if accounts:
                        return len([a for a in accounts if float(a.get("uiAmount", 0) or 0) > 0])
        except Exception as e:
            logger.debug(f"Helius getTokenLargestAccounts error for {address}: {e}")
        return None

    async def get_bonding_curve_state(self, mint_address: str) -> Optional[dict]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "bonding-curve",
                "method": "getAccountInfo",
                "params": [
                    mint_address,
                    {"encoding": "base64", "commitment": "confirmed"},
                ],
            }
            async with self.session.post(
                self.das_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"Bonding curve state error for {mint_address}: {e}")
        return None

    async def get_token_supply(self, mint_address: str) -> Optional[float]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "token-supply",
                "method": "getTokenSupply",
                "params": [mint_address],
            }
            async with self.session.post(
                self.das_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = data.get("result", {}).get("value", {})
                    amt = val.get("uiAmount") or val.get("uiAmountString")
                    if amt is not None:
                        return float(amt)
        except Exception as e:
            logger.debug(f"Token supply error for {mint_address}: {e}")
        return None

    async def detect_migration(self, mint_address: str) -> bool:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "raydium-pools",
                "method": "getProgramAccounts",
                "params": [
                    RAYDIUM_AMM_V4,
                    {"encoding": "base64", "filters": [{"dataSize": 752}]},
                ],
            }
            async with self.session.post(
                self.das_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = data.get("result", []) or []
                    for _acct in accounts[:50]:
                        return True
        except Exception as e:
            logger.debug(f"Migration detect error for {mint_address}: {e}")
        return False
