import aiohttp
import asyncio
import base64
import logging
import struct
import time
from typing import Optional
from functools import wraps
from config import config

logger = logging.getLogger("helius_client")

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
PUMP_FUN_FRONTEND = "https://frontend-api.pump.fun"
PUMP_FUN_MIGRATION_SOL = 85.0

# Rate limiter state - VERY conservative to prevent quota exhaustion
# Pre-migration: bot exhausted quota with 10 req/sec
# Now: max 2 req/min to ensure key survives
_rate_limit_state = {
    "tokens": 2.0,  # Only 2 tokens = 2 requests
    "last_refill": time.time(),
    "max_tokens": 2.0,
    "refill_rate": 0.033,  # 1 token per 30 seconds = 2 per minute
    "total_requests": 0,
    "daily_limit": 500,  # Safety cap for the day
}

# Request cache (TTL 5 minutes)
_request_cache = {}
_CACHE_TTL = 300  # 5 minutes


def _rate_limited(method):
    """Decorator to apply token bucket rate limiting."""
    @wraps(method)
    async def wrapper(self, *args, **kwargs):
        # Check daily limit
        _rate_limit_state["total_requests"] += 1
        if _rate_limit_state["total_requests"] > _rate_limit_state["daily_limit"]:
            logger.warning(f"Daily limit reached ({_rate_limit_state['daily_limit']}), skipping Helius request")
            return None
        
        now = time.time()
        # Refill tokens
        elapsed = now - _rate_limit_state["last_refill"]
        _rate_limit_state["tokens"] = min(
            _rate_limit_state["max_tokens"],
            _rate_limit_state["tokens"] + elapsed * _rate_limit_state["refill_rate"]
        )
        _rate_limit_state["last_refill"] = now

        if _rate_limit_state["tokens"] < 1:
            wait_time = (1 - _rate_limit_state["tokens"]) / _rate_limit_state["refill_rate"]
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            _rate_limit_state["tokens"] = 0

        _rate_limit_state["tokens"] -= 1

        # Try with exponential backoff on 429
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await method(self, *args, **kwargs)
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"Helius 429 rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
                raise
        return None
    return wrapper


def _cached(cache_key_func, ttl=_CACHE_TTL):
    """Decorator for request caching."""
    def decorator(method):
        @wraps(method)
        async def wrapper(self, *args, **kwargs):
            cache_key = cache_key_func(self, *args, **kwargs)
            now = time.time()
            if cache_key in _request_cache:
                cached_data, cached_time = _request_cache[cache_key]
                if now - cached_time < ttl:
                    logger.debug(f"Cache hit: {cache_key}")
                    return cached_data
            result = await method(self, *args, **kwargs)
            if result is not None:
                _request_cache[cache_key] = (result, now)
            return result
        return wrapper
    return decorator

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
PUMP_FUN_FRONTEND = "https://frontend-api.pump.fun"
PUMP_FUN_MIGRATION_SOL = 85.0


def _decode_bonding_curve(data_b64: str) -> Optional[dict]:
    try:
        raw = base64.b64decode(data_b64)
        if len(raw) < 49:
            return None
        virtual_token_reserves = struct.unpack_from("<Q", raw, 8)[0]
        virtual_sol_reserves = struct.unpack_from("<Q", raw, 16)[0]
        real_token_reserves = struct.unpack_from("<Q", raw, 24)[0]
        real_sol_reserves = struct.unpack_from("<Q", raw, 32)[0]
        token_total_supply = struct.unpack_from("<Q", raw, 40)[0]
        complete = bool(raw[48])
        real_sol = real_sol_reserves / 1e9
        return {
            "virtual_token_reserves": virtual_token_reserves,
            "virtual_sol_reserves": virtual_sol_reserves,
            "real_token_reserves": real_token_reserves,
            "real_sol_reserves": real_sol,
            "token_total_supply": token_total_supply,
            "complete": complete,
            "progress_pct": min(100.0, (real_sol / PUMP_FUN_MIGRATION_SOL) * 100.0),
        }
    except Exception as e:
        logger.debug(f"bonding curve decode error: {e}")
        return None


class HeliusClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.api_key = config.helius_api_key
        self.base_url = "https://mainnet.helius-rpc.com"
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    @_rate_limited
    @_cached(lambda self, address: f"txs_{address}")
    async def get_launch_transactions(self, address: str) -> list:
        try:
            url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={self.api_key}&limit=20&type=SWAP"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Helius tx error for {address}: {e}")
        return []

    @_rate_limited
    @_cached(lambda self, address: f"holders_{address}")
    async def get_holder_count(self, address: str) -> Optional[int]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "helius-largest",
                "method": "getTokenLargestAccounts",
                "params": [address],
            }
            async with self.session.post(
                self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = (data.get("result") or {}).get("value", [])
                    if accounts:
                        meaningful = [a for a in accounts if float(a.get("uiAmount", 0) or 0) > 1]
                        count = len(meaningful) if meaningful else len(accounts)
                        if count > 0:
                            return count
        except Exception as e:
            logger.debug(f"Helius getTokenLargestAccounts error for {address}: {e}")
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "helius-holders",
                "method": "getTokenAccounts",
                "params": {
                    "mint": address,
                    "limit": 1000,
                    "options": {"showZeroBalance": False, "showNativeBalance": False},
                },
            }
            async with self.session.post(
                self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {}) or {}
                    accounts = result.get("token_accounts", []) or result.get("accounts", []) or []
                    if accounts:
                        meaningful = [a for a in accounts if float(a.get("uiAmount", 0) or 0) > 1]
                        return len(meaningful) if meaningful else len(accounts)
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
                self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = (data.get("result", {}) or {}).get("value", [])
                    if accounts:
                        return len([a for a in accounts if float(a.get("uiAmount", 0) or 0) > 0])
        except Exception as e:
            logger.debug(f"Helius getTokenLargestAccounts error for {address}: {e}")
        return None

    @_rate_limited
    @_cached(lambda self, address: f"bc_{address}")
    async def get_bonding_curve_state(self, mint_address: str) -> Optional[dict]:
        try:
            async with self.session.get(
                f"{PUMP_FUN_FRONTEND}/coins/{mint_address}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("mint") == mint_address:
                        return {
                            "source": "pump_frontend",
                            "complete": bool(data.get("complete", False)),
                            "real_sol_reserves": float(data.get("real_sol_reserves", 0) or 0) / 1e9,
                            "virtual_sol_reserves": float(data.get("virtual_sol_reserves", 0) or 0) / 1e9,
                            "virtual_token_reserves": float(data.get("virtual_token_reserves", 0) or 0),
                            "progress_pct": min(100.0, (float(data.get("real_sol_reserves", 0) or 0) / 1e9 / PUMP_FUN_MIGRATION_SOL) * 100.0),
                            "usd_market_cap": float(data.get("usd_market_cap", 0) or 0),
                            "raydium_pool": data.get("raydium_pool"),
                        }
        except Exception as e:
            logger.debug(f"pump.fun frontend fetch error for {mint_address}: {e}")
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "pump-pools",
                "method": "getProgramAccounts",
                "params": [
                    PUMP_FUN_PROGRAM,
                    {
                        "encoding": "base64",
                        "filters": [
                            {"memcmp": {"offset": 0, "bytes": mint_address}},
                        ],
                    },
                ],
            }
            async with self.session.post(
                self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = data.get("result", []) or []
                    if not accounts:
                        return None
                    bc_account = max(accounts, key=lambda a: a.get("account", {}).get("lamports", 0))
                    account_data = bc_account.get("account", {}).get("data", [])
                    if isinstance(account_data, list) and len(account_data) > 0:
                        decoded = _decode_bonding_curve(account_data[0])
                        if decoded:
                            decoded["source"] = "onchain"
                            decoded["usd_market_cap"] = 0.0
                            decoded["raydium_pool"] = None
                            return decoded
        except Exception as e:
            logger.debug(f"onchain bonding curve fetch error for {mint_address}: {e}")
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
                self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = (data.get("result", {}) or {}).get("value", {}) or {}
                    amt = val.get("uiAmount") if val.get("uiAmount") is not None else val.get("uiAmountString")
                    if amt is not None:
                        return float(amt)
        except Exception as e:
            logger.debug(f"Token supply error for {mint_address}: {e}")
        return None

    async def detect_migration(self, mint_address: str) -> bool:
        try:
            async with self.session.get(
                f"{PUMP_FUN_FRONTEND}/coins/{mint_address}",
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("mint") == mint_address:
                        return bool(data.get("complete", False))
        except Exception as e:
            logger.debug(f"pump.fun migration check error for {mint_address}: {e}")
        return False

    async def get_whale_transactions(self, address: str, min_sol: float = 5.0) -> dict:
        result = {"whale_buys": 0, "whale_sells": 0, "largest_buy_sol": 0.0, "whale_wallets": set()}
        try:
            txs = await self.get_launch_transactions(address)
            for tx in txs:
                try:
                    sol_amount = 0
                    for transfer in tx.get("tokenTransfers", []):
                        amt = float(transfer.get("tokenAmount", 0) or 0)
                        sol_amount += amt
                    native = tx.get("nativeTransfers", [])
                    for nt in native:
                        sol_amount += float(nt.get("amount", 0) or 0) / 1e9

                    if sol_amount < min_sol:
                        continue

                    tx_type = tx.get("type", "")
                    fee_payer = tx.get("feePayer", "")
                    source = tx.get("source", "")

                    if tx_type == "SWAP" and source == "PUMP_FUN":
                        result["whale_buys"] += 1
                        result["whale_wallets"].add(fee_payer)
                        if sol_amount > result["largest_buy_sol"]:
                            result["largest_buy_sol"] = sol_amount
                    elif tx_type == "SWAP":
                        result["whale_sells"] += 1
                        result["whale_wallets"].add(fee_payer)
                except Exception:
                    continue
            result["whale_wallets"] = len(result["whale_wallets"])
        except Exception as e:
            logger.debug(f"Whale transaction error for {address}: {e}")
        return result

    async def get_deployer_history(self, deployer_address: str) -> dict:
        result = {"total_launches": 0, "rugged_count": 0, "success_rate": 0.0}
        if not deployer_address:
            return result
        try:
            url = f"https://api.helius.xyz/v0/addresses/{deployer_address}/transactions?api-key={self.api_key}&limit=50&type=TRANSFER"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    txs = await resp.json()
                    seen_mints = set()
                    for tx in txs:
                        for transfer in tx.get("tokenTransfers", []):
                            mint = transfer.get("mint", "")
                            if mint and mint not in seen_mints:
                                seen_mints.add(mint)
                    result["total_launches"] = len(seen_mints)
        except Exception as e:
            logger.debug(f"Deployer history error for {deployer_address}: {e}")
        return result
