import asyncio
import aiohttp
import json
import logging
import time
from typing import Callable, Optional
from config import config

logger = logging.getLogger("pre_migration")

PUMP_FUN_API = "https://frontend-api-v3.pump.fun"
PUMP_FUN_WS = "wss://pumpportal.fun/api/data"

HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={config.helius_api_key}"

BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class PreMigrationDetector:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.seen_tokens = {}
        self.on_pump_callback: Optional[Callable] = None
        self._running = False
        self._ws = None
        self.tracked_tokens = {}
        self.min_sol_for_migration = 85.0
        self.pump_threshold_pct = 70.0
        self._last_api_call = 0
        self._min_api_delay = 0.5

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_api_call
        if elapsed < self._min_api_delay:
            await asyncio.sleep(self._min_api_delay - elapsed)
        self._last_api_call = time.time()

    def set_callback(self, callback: Callable):
        self.on_pump_callback = callback

    async def start(self):
        self._running = True
        logger.info("Pre-migration detector started")
        await asyncio.gather(
            self._monitor_bonding_curves(),
            self._scan_new_tokens(),
        )

    async def _scan_new_tokens(self):
        while self._running:
            try:
                await self._rate_limit()
                url = f"{PUMP_FUN_API}/coins?limit=50&offset=0&sort=created_timestamp&order=DESC"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for token in data:
                            mint = token.get("mint")
                            if not mint:
                                continue

                            if mint in self.seen_tokens:
                                continue

                            self.seen_tokens[mint] = token

                            sol_raised = float(token.get("real_sol_reserves", 0) or 0) / 1e9
                            complete = token.get("complete", False)

                            if not complete and sol_raised > 0:
                                progress = (sol_raised / self.min_sol_for_migration) * 100
                                if progress >= 50:
                                    logger.info(
                                        f"📊 Bonding Curve: {token.get('symbol', '???')} | "
                                        f"Progress: {progress:.0f}% | SOL: {sol_raised:.1f}"
                                    )
                                    await self._analyze_token(token, progress)

            except Exception as e:
                logger.debug(f"Scan error: {e}")

            await asyncio.sleep(10)

    async def _monitor_bonding_curves(self):
        while self._running:
            try:
                to_check = list(self.tracked_tokens.keys())
                for mint in to_check:
                    await self._rate_limit()
                    info = await self._get_bonding_curve_info(mint)
                    if info:
                        self.tracked_tokens[mint] = info

                        progress = info.get("progress", 0)
                        if progress >= 80:
                            logger.info(
                                f"🚀 NEAR MIGRATION: {info.get('symbol', '???')} | "
                                f"Progress: {progress:.0f}% | "
                                f"SOL: {info.get('sol_raised', 0):.1f}"
                            )
                            if self.on_pump_callback:
                                asyncio.create_task(self.on_pump_callback(info))

            except Exception as e:
                logger.debug(f"Monitor error: {e}")

            await asyncio.sleep(5)

    async def _get_bonding_curve_info(self, mint: str) -> Optional[dict]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [mint, {"encoding": "jsonParsed"}]
            }
            async with self.session.post(
                HELIUS_RPC, json=payload,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    account = data.get("result", {}).get("value")
                    if not account:
                        return None

                    parsed = account.get("data", {}).get("parsed", {})
                    info = parsed.get("info", {})
                    keys = info.get("keys", [])

                    sol_amount = 0
                    for k in keys:
                        if k.get("pubkey") == "So11111111111111111111111111111111111111112":
                            sol_amount = k.get("lamports", 0) / 1e9

                    progress = (sol_amount / self.min_sol_for_migration) * 100

                    return {
                        "mint": mint,
                        "sol_raised": sol_amount,
                        "progress": min(progress, 100),
                        "complete": progress >= 100,
                    }

        except Exception as e:
            logger.debug(f"Bonding curve check error for {mint}: {e}")
        return None

    async def _analyze_token(self, token: dict, progress: float):
        mint = token.get("mint")
        if not mint or mint in self.tracked_tokens:
            return

        self.tracked_tokens[mint] = {
            "mint": mint,
            "symbol": token.get("symbol", "???"),
            "name": token.get("name", "Unknown"),
            "progress": progress,
            "sol_raised": float(token.get("sol_raised", 0) or 0),
            "timestamp": time.time(),
        }

        if progress >= 70:
            logger.info(
                f"📊 TRACKING: {token.get('symbol', '???')} | "
                f"Progress: {progress:.0f}% | "
                f"Name: {token.get('name', 'Unknown')}"
            )

    async def get_token_info(self, mint: str) -> Optional[dict]:
        return self.tracked_tokens.get(mint)

    def get_stats(self) -> dict:
        return {
            "tracked": len(self.tracked_tokens),
            "seen": len(self.seen_tokens),
        }
