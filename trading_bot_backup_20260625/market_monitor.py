import asyncio
import aiohttp
import logging
import time
from typing import Optional, List, Dict
from config import config

logger = logging.getLogger("market")

PUMP_FUN_API = "https://frontend-api-v3.pump.fun"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={config.helius_api_key}"
GECKO_API = "https://api.geckoterminal.com/api/v2"
RAYDIUM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"


class MarketMonitor:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.seen_tokens: Dict[str, float] = {}
        self._token_cache: Dict[str, dict] = {}
        self.last_prices: Dict[str, float] = {}
        self.last_volumes: Dict[str, float] = {}
        self.last_liquidities: Dict[str, float] = {}
        self.last_checks: Dict[str, float] = {}
        self.migration_cache: Dict[str, dict] = {}
        self.scan_stats = {
            "total_scans": 0,
            "tokens_found": 0,
            "metrics_fetched": 0,
            "pumpfun_new": 0,
            "pumpfun_boosted": 0,
            "migrations_detected": 0,
        }
        self._last_api_call = 0
        self._min_api_delay = 2.0
        self._last_full_scan = 0
        self._full_scan_interval = 30

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_api_call
        if elapsed < self._min_api_delay:
            await asyncio.sleep(self._min_api_delay - elapsed)
        self._last_api_call = time.time()

    async def _safe_get(self, url, headers=None, timeout=10):
        await self._rate_limit()
        try:
            async with self.session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    logger.debug(f"Rate limited on {url}, backing off")
                    await asyncio.sleep(5)
                return None
        except Exception as e:
            logger.debug(f"GET {url[:80]}... error: {e}")
            return None

    async def fetch_new_solana_pairs(self) -> list:
        self.scan_stats["total_scans"] += 1

        now = time.time()
        if now - self._last_full_scan < self._full_scan_interval:
            cached = [self._token_cache.get(m, m) for m in self.seen_tokens if m in self._token_cache]
            return cached if cached else list(self.seen_tokens.keys())

        self._last_full_scan = now

        gecko_tokens = await self._get_gecko_new_pools()
        if gecko_tokens:
            for token in gecko_tokens:
                mint = token.get("mint")
                if not mint:
                    continue
                if mint not in self.seen_tokens:
                    self.seen_tokens[mint] = time.time()
                    self.scan_stats["pumpfun_new"] += 1
                self._token_cache[mint] = token

        now = time.time()
        self.seen_tokens = {k: v for k, v in self.seen_tokens.items() if now - v < 7200}
        self._token_cache = {k: v for k, v in self._token_cache.items() if k in self.seen_tokens}

        result = [self._token_cache.get(m, m) for m in self.seen_tokens]
        self.scan_stats["tokens_found"] = len(result)
        if result:
            logger.debug(
                f"Scan: {len(result)} new tokens "
                f"(total unique: {len(self.seen_tokens)})"
            )
        return result

    async def _get_gecko_new_pools(self) -> list:
        tokens = []
        for page in [1, 2]:
            try:
                await self._rate_limit()
                url = f"{GECKO_API}/networks/solana/new_pools?page={page}"
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15),
                    headers={"Accept": "application/json"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pools = data.get("data", [])
                        for pool in pools:
                            attrs = pool.get("attributes", {})
                            rels = pool.get("relationships", {})
                            base_token = rels.get("base_token", {}).get("data", {}).get("id", "")
                            mint = base_token.replace("solana_", "") if base_token.startswith("solana_") else ""
                            if not mint:
                                continue

                            price_usd = float(attrs.get("base_token_price_usd", 0) or 0)
                            fdv = float(attrs.get("fdv_usd", 0) or 0)
                            liquidity = float(attrs.get("reserve_in_usd", 0) or 0)
                            volume_data = attrs.get("volume_usd", {})
                            vol_5m = float(volume_data.get("m5", 0) or 0)
                            vol_1h = float(volume_data.get("h1", 0) or 0)
                            price_changes = attrs.get("price_change_percentage", {})
                            pc_5m = float(price_changes.get("m5", 0) or 0)
                            pc_1h = float(price_changes.get("h1", 0) or 0)
                            txns = attrs.get("transactions", {})
                            buyers_5m = int(txns.get("m5", {}).get("buyers", 0) or 0)
                            sellers_5m = int(txns.get("m5", {}).get("sellers", 0) or 0)
                            buys_5m = int(txns.get("m5", {}).get("buys", 0) or 0)
                            sells_5m = int(txns.get("m5", {}).get("sells", 0) or 0)
                            buyers_1h = int(txns.get("h1", {}).get("buyers", 0) or 0)
                            sellers_1h = int(txns.get("h1", {}).get("sellers", 0) or 0)
                            buys_1h = int(txns.get("h1", {}).get("buys", 0) or 0)
                            sells_1h = int(txns.get("h1", {}).get("sells", 0) or 0)
                            created_at = attrs.get("pool_created_at", "")
                            name = attrs.get("name", "??? / SOL")
                            symbol = name.split("/")[0].strip() if "/" in name else name

                            token = {
                                "mint": mint,
                                "symbol": symbol,
                                "name": symbol,
                                "price_usd": price_usd,
                                "fdv": fdv,
                                "liquidity": liquidity,
                                "volume_5m": vol_5m,
                                "volume_1h": vol_1h,
                                "price_change_5m": pc_5m,
                                "price_change_1h": pc_1h,
                                "buyers_5m": buyers_5m,
                                "sellers_5m": sellers_5m,
                                "buys_5m": buys_5m,
                                "sells_5m": sells_5m,
                                "buyers_1h": buyers_1h,
                                "sellers_1h": sellers_1h,
                                "buys_1h": buys_1h,
                                "sells_1h": sells_1h,
                                "created_at": created_at,
                                "source": "gecko",
                                "transactions": txns,
                            }
                            tokens.append(token)
                    elif resp.status == 429:
                        logger.debug(f"Gecko rate limited page {page}, backing off")
                        await asyncio.sleep(5)
                    else:
                        logger.debug(f"Gecko page {page} status {resp.status}")
                    break
            except asyncio.TimeoutError:
                logger.debug(f"Gecko new pools timeout page {page}")
            except Exception as e:
                logger.debug(f"Gecko new pools error page {page}: {e}")
        return tokens

    async def fetch_pair_data(self, token_address: str, pumpfun_token: dict = None) -> Optional[dict]:
        if pumpfun_token and pumpfun_token.get("source") == "gecko":
            return self._build_metrics_from_gecko(token_address, pumpfun_token)

        helius_data = await self._get_helius_token_data(token_address)
        if not helius_data and pumpfun_token:
            helius_data = self._get_pumpfun_data(pumpfun_token)
        if not helius_data:
            return None

        price_data = await self._get_jupiter_price(token_address)
        price_usd = 0
        if price_data and price_data.get("price"):
            price_usd = float(price_data["price"])

        token_supply = helius_data.get("supply", 0) / (10 ** helius_data.get("decimals", 9)) if helius_data.get("supply", 0) > 0 else 0
        market_cap = price_usd * token_supply if price_usd > 0 and token_supply > 0 else 0
        sol_raised = helius_data.get("sol_raised", 0)
        progress = helius_data.get("progress", 0)
        if market_cap == 0 and sol_raised > 0:
            market_cap = sol_raised * 1000

        prev_price = self.last_prices.get(token_address, price_usd)
        self.last_prices[token_address] = price_usd
        price_change_now = 0.0
        if prev_price > 0 and price_usd > 0:
            price_change_now = ((price_usd - prev_price) / prev_price) * 100

        age_seconds = 0
        token_created = helius_data.get("created_at")
        if token_created:
            try:
                if isinstance(token_created, str):
                    from datetime import datetime
                    dt = datetime.fromisoformat(token_created.replace("Z", "+00:00"))
                    age_seconds = time.time() - dt.timestamp()
                else:
                    age_seconds = time.time() - token_created
            except Exception:
                pass

        return {
            "address": token_address,
            "symbol": helius_data.get("symbol", "???"),
            "name": helius_data.get("name", "Unknown"),
            "price_usd": price_usd,
            "volume_5m": 0,
            "volume_1h": 0,
            "liquidity": market_cap * 0.05 if market_cap > 0 else 0,
            "fdv": market_cap,
            "price_change_5m": price_change_now,
            "price_change_1h": 0,
            "price_change_now": price_change_now,
            "volume_change": 0,
            "liquidity_change": 0,
            "age_seconds": age_seconds,
            "dex_id": "pumpfun",
            "pair_address": "",
            "url": f"https://pump.fun/coin/{token_address}",
            "holder_count": helius_data.get("holder_count", 0),
            "token_supply": token_supply,
            "bonding_curve_progress": progress,
            "sol_raised": sol_raised,
            "complete": helius_data.get("complete", False),
            "buys_5m": 0,
            "sells_5m": 0,
            "buys_1h": 0,
            "sells_1h": 0,
        }

    def _build_metrics_from_gecko(self, token_address: str, gecko_token: dict) -> Optional[dict]:
        price_usd = gecko_token.get("price_usd", 0)
        fdv = gecko_token.get("fdv", 0)
        liquidity = gecko_token.get("liquidity", 0)
        volume_5m = gecko_token.get("volume_5m", 0)
        volume_1h = gecko_token.get("volume_1h", 0)
        pc_5m = gecko_token.get("price_change_5m", 0)
        pc_1h = gecko_token.get("price_change_1h", 0)

        prev_price = self.last_prices.get(token_address, price_usd)
        self.last_prices[token_address] = price_usd
        price_change_now = 0.0
        if prev_price > 0 and price_usd > 0:
            price_change_now = ((price_usd - prev_price) / prev_price) * 100

        prev_vol = self.last_volumes.get(token_address, volume_5m)
        self.last_volumes[token_address] = volume_5m
        volume_change = 0.0
        if prev_vol > 0 and volume_5m > 0:
            volume_change = ((volume_5m - prev_vol) / prev_vol) * 100

        prev_liq = self.last_liquidities.get(token_address, liquidity)
        self.last_liquidities[token_address] = liquidity
        liquidity_change = 0.0
        if prev_liq > 0 and liquidity > 0:
            liquidity_change = ((liquidity - prev_liq) / prev_liq) * 100

        age_seconds = 0
        created_at = gecko_token.get("created_at", "")
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_seconds = time.time() - dt.timestamp()
            except Exception:
                pass

        return {
            "address": token_address,
            "symbol": gecko_token.get("symbol", "???"),
            "name": gecko_token.get("name", "Unknown"),
            "price_usd": price_usd,
            "volume_5m": volume_5m,
            "volume_1h": volume_1h,
            "liquidity": liquidity,
            "fdv": fdv,
            "price_change_5m": pc_5m if pc_5m else price_change_now,
            "price_change_1h": pc_1h,
            "price_change_now": price_change_now if price_change_now else pc_5m,
            "volume_change": volume_change,
            "liquidity_change": liquidity_change,
            "age_seconds": age_seconds,
            "dex_id": "raydium",
            "pair_address": "",
            "url": f"https://pump.fun/coin/{token_address}",
            "holder_count": 0,
            "token_supply": 0,
            "bonding_curve_progress": 0,
            "sol_raised": 0,
            "complete": False,
            "buys_5m": gecko_token.get("buys_5m", 0),
            "sells_5m": gecko_token.get("sells_5m", 0),
            "buys_1h": gecko_token.get("buys_1h", 0),
            "sells_1h": gecko_token.get("sells_1h", 0),
            "buyers_5m": gecko_token.get("buyers_5m", 0),
            "sellers_5m": gecko_token.get("sellers_5m", 0),
            "buyers_1h": gecko_token.get("buyers_1h", 0),
            "sellers_1h": gecko_token.get("sellers_1h", 0),
            "transactions": gecko_token.get("transactions", {}),
        }

    async def _get_helius_token_data(self, mint: str) -> Optional[dict]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": mint},
            }
            await self._rate_limit()
            async with self.session.post(
                HELIUS_RPC, json=payload,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    content = result.get("content", {}).get("metadata", {})
                    supply = result.get("supply", {})

                    symbol = content.get("symbol")
                    if not symbol or symbol == "???":
                        return None

                    return {
                        "symbol": symbol,
                        "name": content.get("name", "Unknown"),
                        "decimals": supply.get("decimals", 9),
                        "supply": int(supply.get("supply", 0)),
                        "holder_count": 0,
                        "created_at": result.get("created_at"),
                    }
        except Exception as e:
            logger.debug(f"Helius token data error for {mint}: {e}")
        return None

    def _get_pumpfun_data(self, token: dict) -> Optional[dict]:
        try:
            mint = token.get("mint")
            symbol = token.get("symbol")
            if not mint or not symbol:
                return None

            sol_raised = float(token.get("real_sol_reserves", 0) or 0) / 1e9
            complete = token.get("complete", False)
            progress = (sol_raised / 85) * 100 if not complete else 100

            return {
                "symbol": symbol,
                "name": token.get("name", "Unknown"),
                "decimals": 9,
                "supply": 0,
                "holder_count": 0,
                "created_at": None,
                "sol_raised": sol_raised,
                "progress": min(progress, 100),
                "complete": complete,
            }
        except Exception:
            return None

    async def _get_jupiter_price(self, mint: str) -> Optional[dict]:
        try:
            url = f"{JUPITER_PRICE}?ids={mint}"
            await self._rate_limit()
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get(mint)
        except Exception as e:
            logger.debug(f"Jupiter price error for {mint}: {e}")
        return None

    async def get_token_metrics(self, token_address: str, pumpfun_token: dict = None) -> Optional[dict]:
        pair = await self.fetch_pair_data(token_address, pumpfun_token)
        if pair:
            self.scan_stats["metrics_fetched"] += 1
            return pair

        if token_address in self._token_cache:
            cached = self._token_cache[token_address]
            if isinstance(cached, dict) and cached.get("source") == "gecko":
                metrics = self._build_metrics_from_gecko(token_address, cached)
                if metrics:
                    self.scan_stats["metrics_fetched"] += 1
                    return metrics

        gecko_metrics = await self._get_gecko_token_metrics(token_address)
        if gecko_metrics:
            self.scan_stats["metrics_fetched"] += 1
            return gecko_metrics

        return None

    async def _get_gecko_token_metrics(self, token_address: str) -> Optional[dict]:
        try:
            await self._rate_limit()
            url = f"{GECKO_API}/networks/solana/tokens/{token_address}"
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    attrs = data.get("data", {}).get("attributes", {})

                    price_usd = float(attrs.get("price_usd", 0) or 0)
                    fdv = float(attrs.get("fdv_usd", 0) or 0)
                    liquidity = float(attrs.get("total_reserve_in_usd", 0) or 0)
                    name = attrs.get("name", "Unknown")
                    symbol = attrs.get("symbol", "???")

                    if price_usd == 0:
                        return None

                    return {
                        "address": token_address,
                        "symbol": symbol,
                        "name": name,
                        "price_usd": price_usd,
                        "volume_5m": 0,
                        "volume_1h": 0,
                        "liquidity": liquidity,
                        "fdv": fdv,
                        "price_change_5m": 0,
                        "price_change_1h": 0,
                        "price_change_now": 0,
                        "volume_change": 0,
                        "liquidity_change": 0,
                        "age_seconds": 0,
                        "dex_id": "gecko",
                        "pair_address": "",
                        "url": f"https://pump.fun/coin/{token_address}",
                        "holder_count": 0,
                        "token_supply": 0,
                        "bonding_curve_progress": 0,
                        "sol_raised": 0,
                        "complete": False,
                        "buys_5m": 0,
                        "sells_5m": 0,
                        "buys_1h": 0,
                        "sells_1h": 0,
                    }
        except Exception as e:
            logger.debug(f"Gecko token metrics error for {token_address}: {e}")
        return None

    async def fetch_gmgn_data(self, token_address: str) -> Optional[dict]:
        try:
            url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{token_address}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            await self._rate_limit()
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=8),
                headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    token_data = data.get("data", {})
                    if token_data:
                        holder_count = token_data.get("holder_count", 0)
                        top_10_pct = token_data.get("top_10_holder_rate", 0)
                        bundler_pct = token_data.get("bundler_rate", 0)
                        lp_count = token_data.get("lp_count", 0)
                        lock_pct = token_data.get("lock_rate", 0)

                        return {
                            "holder_count": holder_count,
                            "top_10_pct": top_10_pct,
                            "bundler_pct": bundler_pct,
                            "lp_count": lp_count,
                            "lock_pct": lock_pct,
                            "source": "gmgn",
                        }
        except Exception as e:
            logger.debug(f"GMGN fetch error for {token_address}: {e}")
        return None

    async def verify_liquidity_dexscreener(self, token_address: str) -> Optional[dict]:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            await self._rate_limit()
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        best_pair = max(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
                        liq_usd = best_pair.get("liquidity", {}).get("usd", 0)
                        fdv = best_pair.get("fdv", 0)
                        volume_24h = best_pair.get("volume", {}).get("h24", 0)
                        price_usd = float(best_pair.get("priceUsd", 0) or 0)
                        txns = best_pair.get("txns", {})
                        buyers_5m = txns.get("m5", {}).get("buys", 0)
                        sellers_5m = txns.get("m5", {}).get("sells", 0)
                        buyers_1h = txns.get("h1", {}).get("buys", 0)
                        sellers_1h = txns.get("h1", {}).get("sells", 0)

                        return {
                            "liquidity": liq_usd,
                            "fdv": fdv,
                            "volume_24h": volume_24h,
                            "price_usd": price_usd,
                            "buyers_5m": buyers_5m,
                            "sellers_5m": sellers_5m,
                            "buyers_1h": buyers_1h,
                            "sellers_1h": sellers_1h,
                            "lp_count": len(pairs),
                            "source": "dexscreener",
                        }
        except Exception as e:
            logger.debug(f"DexScreener verify error for {token_address}: {e}")
        return None

    async def get_verified_metrics(self, token_address: str, pumpfun_token: dict = None) -> Optional[dict]:
        metrics = await self.get_token_metrics(token_address, pumpfun_token)
        if not metrics:
            return None

        dex_data = await self.verify_liquidity_dexscreener(token_address)
        if dex_data:
            gecko_liq = metrics.get("liquidity", 0)
            dex_liq = dex_data.get("liquidity", 0)

            if dex_liq < 100 and gecko_liq > 1000:
                logger.warning(
                    f"LIQUIDITY MISMATCH {metrics.get('symbol', '?')}: "
                    f"Gecko=${gecko_liq:.0f} vs DexScreener=${dex_liq:.0f} - keeping Gecko (DexScreener not indexed)"
                )
                metrics["liquidity_verified"] = True
                metrics["liquidity_mismatch"] = True
            elif dex_liq > 100 and gecko_liq > 100 and abs(dex_liq - gecko_liq) / max(gecko_liq, 1) > 0.5:
                logger.warning(
                    f"LIQUIDITY DIFF {metrics.get('symbol', '?')}: "
                    f"Gecko=${gecko_liq:.0f} vs DexScreener=${dex_liq:.0f} - using DexScreener"
                )
                metrics["liquidity"] = dex_liq
                metrics["fdv"] = dex_data.get("fdv", metrics.get("fdv", 0))
                metrics["liquidity_verified"] = False
                metrics["liquidity_mismatch"] = True
            else:
                metrics["liquidity_verified"] = True
                metrics["liquidity_mismatch"] = False

            if dex_liq < 100 and gecko_liq < 100:
                metrics["very_low_liquidity"] = True

            dex_lp = dex_data.get("lp_count", 0)
            if dex_lp > 0:
                metrics["lp_count"] = dex_lp

        return metrics
