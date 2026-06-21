"""
pump_collector.py — Background 24/7 pump coin data collector
- Fetches pump coins from DexScreener (7-day pumps)
- Collects complete on-chain data: holders, LP, deployer, security
- Stores only complete data (no partial records)
- Pushes to GitHub periodically
- Analyzes patterns to extract pump filters
"""

import asyncio
import aiohttp
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from config import config

logger = logging.getLogger("meme_bot.pump_collector")

COLLECTOR_DATA_FILE = "pump_collected_data.json"
COLLECTOR_STATE_FILE = "pump_collector_state.json"

DEFAULT_COLLECTOR_DATA = {
    "pump_coins": [],
    "analysis": {
        "total_collected": 0,
        "complete_records": 0,
        "partial_records": 0,
        "last_analysis": None,
        "pump_filters": None,
    },
    "last_fetch": None,
    "last_github_push": None,
}


def _load_collector_data() -> dict:
    try:
        with open(COLLECTOR_DATA_FILE, "r") as f:
            data = json.load(f)
        for k, v in DEFAULT_COLLECTOR_DATA.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_COLLECTOR_DATA))


def _save_collector_data(data: dict):
    try:
        with open(COLLECTOR_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_collector_data error: {e}")


def _load_collector_state() -> dict:
    try:
        with open(COLLECTOR_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"collected_addresses": [], "last_run": None}


def _save_collector_state(state: dict):
    try:
        with open(COLLECTOR_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"save_collector_state error: {e}")


class PumpCollector:
    """Collects pump coin data from DexScreener and on-chain sources."""

    def __init__(self, session: aiohttp.ClientSession, helius=None, birdeye=None, dex=None):
        self.session = session
        self.helius = helius
        self.birdeye = birdeye
        self.dex = dex
        self.collected_count = 0
        self.last_analysis_time = 0
        self.analysis_interval = 3600  # Analyze every 1 hour

    async def fetch_pump_coins_from_dexscreener(self) -> List[Dict]:
        """Fetch pump coins from DexScreener — ALL free API endpoints."""
        pump_coins = []
        seen_addrs = set()

        def _add(addr, source, name=""):
            if addr and addr not in seen_addrs:
                seen_addrs.add(addr)
                pump_coins.append({"address": addr, "source": source, "name": name})

        # 1. Token boosts (top promoted tokens)
        try:
            async with self.session.get("https://api.dexscreener.com/token-boosts/latest/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    for t in await resp.json():
                        if t.get("chainId") == "solana":
                            _add(t.get("tokenAddress", ""), "boosted", t.get("description", ""))
                elif resp.status == 403:
                    logger.warning("DexScreener 403 on boosts, waiting 10s...")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.debug(f"fetch_boosted error: {e}")

        await asyncio.sleep(3)

        # 2. Token profiles (latest)
        try:
            async with self.session.get("https://api.dexscreener.com/token-profiles/latest/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    for t in await resp.json():
                        if t.get("chainId") == "solana":
                            _add(t.get("tokenAddress", ""), "new_profile")
                elif resp.status == 403:
                    logger.warning("DexScreener 403 on profiles, waiting 10s...")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.debug(f"fetch_profiles error: {e}")

        await asyncio.sleep(3)

        # 3. Recently updated profiles
        try:
            async with self.session.get("https://api.dexscreener.com/token-profiles/recent-updates/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    for t in await resp.json():
                        if t.get("chainId") == "solana":
                            _add(t.get("tokenAddress", ""), "recent_update")
                elif resp.status == 403:
                    logger.warning("DexScreener 403 on recent-updates, waiting 10s...")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.debug(f"fetch_recent_updates error: {e}")

        await asyncio.sleep(3)

        # 4. Community takeovers
        try:
            async with self.session.get("https://api.dexscreener.com/community-takeovers/latest/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    for t in await resp.json():
                        if t.get("chainId") == "solana":
                            _add(t.get("tokenAddress", ""), "community_takeover")
                elif resp.status == 403:
                    logger.warning("DexScreener 403 on CTO, waiting 10s...")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.debug(f"fetch_cto error: {e}")

        await asyncio.sleep(3)

        # 5. Trending metas → get tokens from each category
        try:
            async with self.session.get("https://api.dexscreener.com/metas/trending/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    metas = await resp.json()
                    if isinstance(metas, list):
                        for meta in metas[:5]:  # Top 5 categories (reduced)
                            slug = meta.get("slug", "")
                            if slug:
                                try:
                                    async with self.session.get(
                                        f"https://api.dexscreener.com/metas/meta/v1/{slug}",
                                        timeout=aiohttp.ClientTimeout(total=10)
                                    ) as mr:
                                        if mr.status == 200:
                                            mdata = await mr.json()
                                            for p in mdata.get("pairs", []):
                                                if p.get("chainId") == "solana":
                                                    addr = p.get("baseToken", {}).get("address", "")
                                                    _add(addr, f"meta:{slug}", p.get("baseToken", {}).get("name", ""))
                                        elif mr.status == 403:
                                            logger.warning("DexScreener 403 on meta detail, stopping")
                                            break
                                except Exception:
                                    pass
                                await asyncio.sleep(3)  # 3s between meta detail fetches
                elif resp.status == 403:
                    logger.warning("DexScreener 403 on trending metas, waiting 10s...")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.debug(f"fetch_metas error: {e}")

        # 6. Search queries for pump-related tokens (reduced to avoid 403)
        search_queries = [
            "pump", "meme", "solana", "bonk", "pepe", "doge",
            "moon", "rocket", "ai", "wif", "trump",
        ]
        for q in search_queries:
            try:
                async with self.session.get(
                    f"https://api.dexscreener.com/latest/dex/search?q={q}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for p in (data.get("pairs", []) if isinstance(data, dict) else []):
                            if p.get("chainId") == "solana":
                                addr = p.get("baseToken", {}).get("address", "")
                                _add(addr, f"search:{q}", p.get("baseToken", {}).get("name", ""))
                    elif resp.status == 403:
                        logger.warning(f"DexScreener 403 on search '{q}', stopping search queries")
                        break
            except Exception:
                pass
            await asyncio.sleep(3)  # 3s between searches

        # 7. Ads (paid promotions = likely active tokens)
        try:
            async with self.session.get("https://api.dexscreener.com/ads/latest/v1",
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    for t in await resp.json():
                        if t.get("chainId") == "solana":
                            _add(t.get("tokenAddress", ""), "ad")
        except Exception as e:
            logger.debug(f"fetch_ads error: {e}")

        logger.info(f"Fetched {len(pump_coins)} unique Solana tokens from DexScreener "
                    f"(boosted={sum(1 for c in pump_coins if c['source']=='boosted')}, "
                    f"metas={sum(1 for c in pump_coins if 'meta:' in c['source'])}, "
                    f"search={sum(1 for c in pump_coins if 'search:' in c['source'])})")
        return pump_coins

    async def collect_complete_data(self, address: str) -> Optional[Dict]:
        """Collect complete on-chain data for a token. Returns None if data is incomplete."""
        record = {
            "address": address,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "features": {},
            "metadata": {},
            "is_complete": False,
        }

        # 1. Get DexScreener pair data (liquidity, mcap, price, volume)
        try:
            url = f"https://api.dexscreener.com/tokens/v1/solana/{address}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    pairs = await resp.json()
                    if pairs and isinstance(pairs, list) and len(pairs) > 0:
                        pair = pairs[0]
                        base_token = pair.get("baseToken", {})
                        record["metadata"]["name"] = base_token.get("name", "")
                        record["metadata"]["symbol"] = base_token.get("symbol", "")

                        liq = pair.get("liquidity", {})
                        record["features"]["liquidity"] = liq.get("usd", 0) if isinstance(liq, dict) else 0
                        record["features"]["mcap"] = pair.get("marketCap", 0) or pair.get("fdv", 0)

                        price_change = pair.get("priceChange", {})
                        record["features"]["price_change_24h"] = price_change.get("h24", 0) if isinstance(price_change, dict) else 0

                        txns = pair.get("txns", {})
                        h1 = txns.get("h1", {})
                        h6 = txns.get("h6", {})
                        h24 = txns.get("h24", {})

                        record["features"]["buys_1h"] = h1.get("buys", 0) if isinstance(h1, dict) else 0
                        record["features"]["sells_1h"] = h1.get("sells", 0) if isinstance(h1, dict) else 0
                        record["features"]["buys_6h"] = h6.get("buys", 0) if isinstance(h6, dict) else 0
                        record["features"]["sells_6h"] = h6.get("sells", 0) if isinstance(h6, dict) else 0
                        record["features"]["buys_24h"] = h24.get("buys", 0) if isinstance(h24, dict) else 0
                        record["features"]["sells_24h"] = h24.get("sells", 0) if isinstance(h24, dict) else 0

                        b1h = record["features"]["buys_1h"]
                        s1h = record["features"]["sells_1h"]
                        record["features"]["buy_sell_ratio"] = round(b1h / max(s1h, 1), 2)

                        record["features"]["volume"] = pair.get("volume", {}).get("h24", 0) if isinstance(pair.get("volume"), dict) else 0

                        # Price info
                        record["features"]["price"] = float(pair.get("priceUsd", 0) or 0)

                        # Age calculation
                        pair_created = pair.get("pairCreatedAt", 0)
                        if pair_created:
                            age_hours = (time.time() * 1000 - pair_created) / 3600000
                            record["features"]["age_hours"] = round(age_hours, 1)
                        else:
                            record["features"]["age_hours"] = -1

                        # Deployer
                        record["metadata"]["deployer"] = pair.get("deployer", "") or pair.get("creatorAddress", "") or ""

                        # DexId / PairAddress
                        record["metadata"]["dex_id"] = pair.get("dexId", "")
                        record["metadata"]["pair_address"] = pair.get("pairAddress", "")

                        # Migration status
                        record["features"]["is_migrated"] = 1 if pair.get("dexId") != "pump.fun" else 0
                    else:
                        logger.debug(f"No pair data for {address}")
                        return None
        except Exception as e:
            logger.debug(f"DexScreener fetch error for {address}: {e}")
            return None

        # 2. Get Helius holder count (skip if rate limited)
        if self.helius:
            try:
                holders = await self.helius.get_holder_count(address)
                record["features"]["holders"] = holders if holders else 0
            except Exception as e:
                logger.debug(f"Helius holder count error for {address}: {e}")
                record["features"]["holders"] = 0

            # Skip deployer history to save Helius quota
            record["metadata"]["deployer_launches"] = 0
            record["metadata"]["deployer_rugged"] = 0
        else:
            record["features"]["holders"] = 0

        # 3. Get Birdeye LP analysis and security
        if self.birdeye and self.birdeye.enabled:
            try:
                lp_data = await self.birdeye.get_lp_analysis(address, record["metadata"].get("deployer", ""))
                if lp_data:
                    record["features"]["lp_providers_count"] = lp_data.get("lp_providers_count", 0)
                    record["features"]["deployer_has_lp"] = 1 if lp_data.get("deployer_has_lp") else 0
                    record["features"]["lp_locked"] = lp_data.get("top_lp_holder_pct", 0)
                else:
                    record["features"]["lp_providers_count"] = 0
                    record["features"]["deployer_has_lp"] = 0
                    record["features"]["lp_locked"] = 0
            except Exception:
                record["features"]["lp_providers_count"] = 0
                record["features"]["deployer_has_lp"] = 0
                record["features"]["lp_locked"] = 0

            try:
                security = await self.birdeye.get_token_security(address)
                if security:
                    record["features"]["freeze_authority"] = 1 if security.get("freeze_authority") else 0
                    record["features"]["mint_authority"] = 1 if security.get("mint_authority") else 0
                else:
                    record["features"]["freeze_authority"] = 0
                    record["features"]["mint_authority"] = 0
            except Exception:
                record["features"]["freeze_authority"] = 0
                record["features"]["mint_authority"] = 0
        else:
            record["features"]["lp_providers_count"] = 0
            record["features"]["deployer_has_lp"] = 0
            record["features"]["lp_locked"] = 0
            record["features"]["freeze_authority"] = 0
            record["features"]["mint_authority"] = 0

        # 4. Derived features
        liq = record["features"].get("liquidity", 0)
        mcap = record["features"].get("mcap", 0)
        record["features"]["liq_pct"] = round((liq / max(mcap, 1)) * 100, 2) if mcap > 0 else 0

        b1h = record["features"].get("buys_1h", 0)
        s1h = record["features"].get("sells_1h", 0)
        record["features"]["buy_sell_momentum"] = round((b1h - s1h) / max(b1h + s1h, 1), 2)

        unique_wallets = record["features"].get("buys_1h", 0) + record["features"].get("sells_1h", 0)
        record["features"]["unique_wallets"] = unique_wallets

        # 5. Check completeness — only require DexScreener data (liq + mcap + buys)
        # Holders/LP may fail due to API rate limits
        is_complete = (
            record["features"].get("liquidity", 0) >= 100 and
            record["features"].get("mcap", 0) > 0
        )

        record["is_complete"] = is_complete
        return record

    async def collect_batch(self, coins: List[Dict], max_collect: int = 50) -> List[Dict]:
        """Collect complete data for a batch of coins. Returns only complete records."""
        collected = []
        state = _load_collector_state()
        collected_addrs = set(state.get("collected_addresses", []))

        # Filter out already collected
        new_coins = [c for c in coins if c["address"] not in collected_addrs]

        if not new_coins:
            logger.info("All coins already collected, skipping batch")
            return []

        logger.info(f"Collecting data for {min(max_collect, len(new_coins))} new coins...")

        batch_start = time.time()
        for i, coin in enumerate(new_coins[:max_collect]):
            try:
                record = await self.collect_complete_data(coin["address"])
                if record and record["is_complete"]:
                    collected.append(record)
                    collected_addrs.add(coin["address"])
                    if (i + 1) % 10 == 0:
                        logger.info(f"📊 Progress: {i+1}/{min(max_collect, len(new_coins))} "
                                  f"({len(collected)} complete)")
                elif record:
                    logger.debug(f"⚠️ Incomplete: {coin.get('name','?')} — "
                               f"liq=${record['features'].get('liquidity', 0):.0f} "
                               f"holders={record['features'].get('holders', 0)}")
                # Rate limit: 5s per token (12/min, well under 60/min even with main bot)
                await asyncio.sleep(5)
            except Exception as e:
                logger.debug(f"Collect error for {coin['address']}: {e}")
                continue

        elapsed = time.time() - batch_start
        rate = len(collected) / (elapsed / 60) if elapsed > 0 else 0
        logger.info(f"✅ Batch complete: {len(collected)}/{min(max_collect, len(new_coins))} "
                   f"in {elapsed:.0f}s ({rate:.1f}/min)")

        # Update state
        state["collected_addresses"] = list(collected_addrs)[-1000:]  # Keep last 1000
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        _save_collector_state(state)

        return collected

    def analyze_pump_patterns(self, records: List[Dict]) -> Dict:
        """Analyze collected pump coins to extract filters and patterns."""
        if not records:
            return {}

        # Separate high-quality records
        complete = [r for r in records if r.get("is_complete")]
        if len(complete) < 5:
            logger.warning(f"Not enough complete records for analysis: {len(complete)}")
            return {}

        # Extract features for analysis
        features_list = [r.get("features", {}) for r in complete]

        # Calculate statistics
        def percentile(data, pct):
            sorted_data = sorted(data)
            idx = int(len(sorted_data) * pct / 100)
            return sorted_data[min(idx, len(sorted_data) - 1)]

        def median(data):
            return percentile(data, 50)

        # Analyze each feature
        liqs = [f.get("liquidity", 0) for f in features_list if f.get("liquidity", 0) > 0]
        mcaps = [f.get("mcap", 0) for f in features_list if f.get("mcap", 0) > 0]
        holders = [f.get("holders", 0) for f in features_list if f.get("holders", 0) > 0]
        bsr = [f.get("buy_sell_ratio", 0) for f in features_list if f.get("buy_sell_ratio", 0) > 0]
        lp_locked = [f.get("lp_locked", 0) for f in features_list]
        liq_pcts = [f.get("liq_pct", 0) for f in features_list if f.get("liq_pct", 0) > 0]
        lp_providers = [f.get("lp_providers_count", 0) for f in features_list]
        ages = [f.get("age_hours", 0) for f in features_list if f.get("age_hours", 0) > 0]

        analysis = {
            "total_analyzed": len(complete),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "statistics": {
                "liquidity": {
                    "median": round(median(liqs), 0) if liqs else 0,
                    "p25": round(percentile(liqs, 25), 0) if liqs else 0,
                    "p75": round(percentile(liqs, 75), 0) if liqs else 0,
                    "min": min(liqs) if liqs else 0,
                    "max": max(liqs) if liqs else 0,
                },
                "mcap": {
                    "median": round(median(mcaps), 0) if mcaps else 0,
                    "p25": round(percentile(mcaps, 25), 0) if mcaps else 0,
                    "p75": round(percentile(mcaps, 75), 0) if mcaps else 0,
                },
                "holders": {
                    "median": round(median(holders), 0) if holders else 0,
                    "p25": round(percentile(holders, 25), 0) if holders else 0,
                    "p75": round(percentile(holders, 75), 0) if holders else 0,
                },
                "buy_sell_ratio": {
                    "median": round(median(bsr), 2) if bsr else 0,
                    "p25": round(percentile(bsr, 25), 2) if bsr else 0,
                    "p75": round(percentile(bsr, 75), 2) if bsr else 0,
                },
                "lp_locked": {
                    "median": round(median(lp_locked), 1) if lp_locked else 0,
                    "p25": round(percentile(lp_locked, 25), 1) if lp_locked else 0,
                    "p75": round(percentile(lp_locked, 75), 1) if lp_locked else 0,
                },
                "liq_pct": {
                    "median": round(median(liq_pcts), 1) if liq_pcts else 0,
                    "p25": round(percentile(liq_pcts, 25), 1) if liq_pcts else 0,
                    "p75": round(percentile(liq_pcts, 75), 1) if liq_pcts else 0,
                },
                "lp_providers_count": {
                    "median": round(median(lp_providers), 0) if lp_providers else 0,
                    "p25": round(percentile(lp_providers, 25), 0) if lp_providers else 0,
                    "p75": round(percentile(lp_providers, 75), 0) if lp_providers else 0,
                },
                "age_hours": {
                    "median": round(median(ages), 1) if ages else 0,
                    "p25": round(percentile(ages, 25), 1) if ages else 0,
                    "p75": round(percentile(ages, 75), 1) if ages else 0,
                },
            },
            # Conservative filters: use P25 as minimum (bottom 25% rejected)
            "recommended_filters": {
                "min_liq": round(percentile(liqs, 25), 0) if liqs else 1500,
                "min_holders": round(percentile(holders, 25), 0) if holders else 10,
                "min_bsr": round(percentile(bsr, 25), 2) if bsr else 1.0,
                "min_lp_locked": round(percentile(lp_locked, 25), 1) if lp_locked else 60,
                "min_liq_pct": round(percentile(liq_pcts, 25), 1) if liq_pcts else 10,
                "min_lp_providers": round(percentile(lp_providers, 25), 0) if lp_providers else 2,
                "max_age_hours": round(percentile(ages, 75), 1) if ages else 12,
            },
        }

        logger.info(f"📊 Analysis complete: {len(complete)} coins analyzed")
        logger.info(f"   Liquidity: median=${analysis['statistics']['liquidity']['median']:.0f}")
        logger.info(f"   Holders: median={analysis['statistics']['holders']['median']:.0f}")
        logger.info(f"   BSR: median={analysis['statistics']['buy_sell_ratio']['median']:.2f}")
        logger.info(f"   LP Locked: median={analysis['statistics']['lp_locked']['median']:.1f}%")

        return analysis

    async def sync_to_github_collector(self):
        """Push collected data to GitHub."""
        try:
            from github_sync import sync_to_github
            await sync_to_github(f"📊 Pump collector: {self.collected_count} coins collected")
            logger.info("✅ Collector data synced to GitHub")
        except Exception as e:
            logger.debug(f"GitHub sync error: {e}")


async def pump_collector_loop(bot):
    """Main background loop for pump coin collection."""
    logger.info("🔄 Pump collector loop starting...")

    collector = PumpCollector(
        bot.session,
        helius=bot.helius,
        birdeye=bot.birdeye,
        dex=bot.dex,
    )

    await asyncio.sleep(30)  # Wait 30s after bot start

    while True:
        try:
            logger.info("🔄 Pump collector cycle started...")

            # 1. Fetch ALL potential pump coins from DexScreener
            pump_coins = await collector.fetch_pump_coins_from_dexscreener()

            if not pump_coins:
                logger.info("No pump coins found from DexScreener")
                await asyncio.sleep(1800)
                continue

            # 2. Collect complete data — slow pace (5s/token) to not interfere with main bot
            new_records = await collector.collect_batch(pump_coins, max_collect=20)

            if new_records:
                # 3. Save to collector data
                data = _load_collector_data()
                existing_addrs = {r["address"] for r in data.get("pump_coins", [])}
                for record in new_records:
                    if record["address"] not in existing_addrs:
                        data["pump_coins"].append(record)
                        existing_addrs.add(record["address"])

                data["last_fetch"] = datetime.now(timezone.utc).isoformat()
                data["analysis"]["total_collected"] = len(data["pump_coins"])
                data["analysis"]["complete_records"] = sum(1 for r in data["pump_coins"] if r.get("is_complete"))
                data["analysis"]["partial_records"] = data["analysis"]["total_collected"] - data["analysis"]["complete_records"]

                _save_collector_data(data)
                collector.collected_count = len(data["pump_coins"])

                logger.info(f"📊 Collector: {data['analysis']['total_collected']} total, "
                          f"{data['analysis']['complete_records']} complete, "
                          f"{data['analysis']['partial_records']} partial")

            # 4. Analyze patterns periodically
            now = time.time()
            if now - collector.last_analysis_time > collector.analysis_interval:
                data = _load_collector_data()
                complete_records = [r for r in data.get("pump_coins", []) if r.get("is_complete")]
                if len(complete_records) >= 10:
                    analysis = collector.analyze_pump_patterns(complete_records)
                    if analysis:
                        data["analysis"]["last_analysis"] = datetime.now(timezone.utc).isoformat()
                        data["analysis"]["pump_filters"] = analysis.get("recommended_filters", {})
                        _save_collector_data(data)
                        collector.last_analysis_time = now

                        # 5. Update bot's signal criteria with new filters
                        try:
                            from learner import load_data, save_data
                            bot_data = load_data()
                            new_filters = analysis.get("recommended_filters", {})
                            if new_filters:
                                # Merge with existing criteria (conservative: take higher value)
                                existing = bot_data.get("model", {}).get("signal_criteria", {})
                                for key, val in new_filters.items():
                                    if key in existing:
                                        existing[key] = max(existing[key], val)
                                    else:
                                        existing[key] = val
                                existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                                existing["sample_size"] = len(complete_records)
                                bot_data["model"]["signal_criteria"] = existing
                                save_data(bot_data)
                                logger.info(f"🎯 Signal criteria updated from collector analysis")
                        except Exception as e:
                            logger.debug(f"Criteria update error: {e}")

            # 6. GitHub sync every 6 hours
            data = _load_collector_data()
            last_push = data.get("last_github_push")
            if not last_push or (datetime.now(timezone.utc) - datetime.fromisoformat(last_push.replace("Z", "+00:00"))).total_seconds() > 21600:
                await collector.sync_to_github_collector()
                data["last_github_push"] = datetime.now(timezone.utc).isoformat()
                _save_collector_data(data)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"pump_collector_loop error: {e}")

        # Wait 30 minutes between cycles
        await asyncio.sleep(1800)


def extract_collected_pump_patterns(data_file: str = COLLECTOR_DATA_FILE) -> List[Dict]:
    """Extract pump patterns from collected data for use in bot_data.json."""
    try:
        with open(data_file, "r") as f:
            collector_data = json.load(f)
    except Exception:
        return []

    patterns = []
    for record in collector_data.get("pump_coins", []):
        if not record.get("is_complete"):
            continue

        features = record.get("features", {})
        metadata = record.get("metadata", {})

        pattern = {
            "address": record.get("address", ""),
            "symbol": metadata.get("symbol", "?"),
            "features": {
                "buy_sell_ratio": features.get("buy_sell_ratio", 0),
                "unique_wallets": features.get("unique_wallets", 0),
                "holders": features.get("holders", 0),
                "liquidity": features.get("liquidity", 0),
                "lp_locked": features.get("lp_locked", 0),
                "lp_providers_count": features.get("lp_providers_count", 0),
                "liq_pct": features.get("liq_pct", 0),
                "mcap": features.get("mcap", 0),
                "volume": features.get("volume", 0),
                "buy_sell_momentum": features.get("buy_sell_momentum", 0),
                "age_hours": features.get("age_hours", 0),
                "price": features.get("price", 0),
            },
            "outcome": "PUMP",
            "ath_multiplier": 0,  # Will be updated when ATH is known
            "learned_at": record.get("collected_at", datetime.now(timezone.utc).isoformat()),
            "source": "pump_collector",
        }
        patterns.append(pattern)

    return patterns


if __name__ == "__main__":
    import aiohttp

    async def test():
        async with aiohttp.ClientSession() as session:
            collector = PumpCollector(session)
            coins = await collector.fetch_pump_coins_from_dexscreener()
            print(f"Found {len(coins)} coins")
            if coins:
                record = await collector.collect_complete_data(coins[0]["address"])
                if record:
                    print(f"Record: {json.dumps(record, indent=2)[:1000]}")
                else:
                    print("No record collected")

    asyncio.run(test())
