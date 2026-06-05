import asyncio
import logging
import os
import aiohttp
from typing import Optional
from config import config

logger = logging.getLogger("social_signals")

TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

class SocialSignalEngine:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.twitter_enabled = bool(TWITTER_BEARER_TOKEN)
        if self.twitter_enabled:
            logger.info("✅ Twitter API enabled")
        else:
            logger.info("ℹ️ Twitter API disabled — using DexScreener socials only")

    async def get_dex_socials(self, token_address: str) -> dict:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        pair = pairs[0]
                        info = pair.get("info", {})
                        socials = info.get("socials", [])
                        websites = info.get("websites", [])
                        return {
                            "twitter": any("twitter" in s.get("url", "").lower() or "x.com" in s.get("url", "").lower() for s in socials),
                            "telegram": any("telegram" in s.get("url", "").lower() or "t.me" in s.get("url", "").lower() for s in socials),
                            "website_count": len(websites),
                            "social_count": len(socials),
                            "has_socials": len(socials) > 0 or len(websites) > 0,
                        }
        except Exception as e:
            logger.warning(f"DexScreener socials error: {e}")
        return {"twitter": False, "telegram": False, "website_count": 0, "social_count": 0, "has_socials": False}

    async def get_twitter_mentions(self, symbol: str, hours: int = 1) -> int:
        if not self.twitter_enabled:
            return 0
        try:
            query = f"${symbol} -is:retweet lang:en"
            url = f"{TWITTER_API_BASE}/tweets/search/recent?query={query}&max_results=100"
            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("meta", {}).get("result_count", 0)
                elif resp.status == 401:
                    logger.warning("Twitter API invalid token — falling back to DexScreener only")
                elif resp.status == 429:
                    logger.warning("Twitter API rate limited")
        except Exception as e:
            logger.warning(f"Twitter mentions error: {e}")
        return 0

    async def get_dex_boost_count(self, token_address: str) -> int:
        try:
            url = f"https://api.dexscreener.com/token-boosts/latest/v1"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    count = sum(1 for item in data if item.get("tokenAddress") == token_address)
                    return count
        except Exception as e:
            logger.warning(f"DexScreener boost count error: {e}")
        return 0

    async def calculate_social_score(self, token_address: str, symbol: str) -> tuple:
        result = {
            "score": 0.0,
            "twitter_mentions": 0,
            "has_telegram": False,
            "has_twitter": False,
            "has_website": False,
            "boost_count": 0,
            "details": "no social signals"
        }

        try:
            socials = await self.get_dex_socials(token_address)
            result["has_telegram"] = socials.get("telegram", False)
            result["has_twitter"] = socials.get("twitter", False)
            result["has_website"] = socials.get("website_count", 0) > 0

            score = 0.0
            if socials.get("has_socials"):
                score += 0.1
            if socials.get("twitter"):
                score += 0.15
            if socials.get("telegram"):
                score += 0.15
            if socials.get("website_count", 0) > 0:
                score += 0.05

            boost_count = await self.get_dex_boost_count(token_address)
            result["boost_count"] = boost_count
            if boost_count > 0:
                score += min(0.2, boost_count * 0.05)

            if self.twitter_enabled:
                mentions = await self.get_twitter_mentions(symbol, hours=1)
                result["twitter_mentions"] = mentions
                if mentions > 50:
                    score += 0.2
                elif mentions > 20:
                    score += 0.15
                elif mentions > 5:
                    score += 0.1
                elif mentions > 0:
                    score += 0.05

            result["score"] = min(1.0, score)
            result["details"] = (
                f"Twitter:{result['twitter_mentions']} "
                f"TG:{'Y' if result['has_telegram'] else 'N'} "
                f"Web:{'Y' if result['has_website'] else 'N'} "
                f"Boost:{boost_count}"
            )
        except Exception as e:
            logger.error(f"Social score calc error: {e}")

        return result["score"], result
