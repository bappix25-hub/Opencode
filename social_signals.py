import asyncio
import logging
import os
import re
import aiohttp
from typing import Optional
from config import config

logger = logging.getLogger("social_signals")

TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

POSITIVE_WORDS = {
    "pump", "bullish", "moon", "gem", "launch", "partnership", "listing",
    "viral", "trending", "buy", "hold", "diamond", "hands", "explosive",
    "breakout", "soar", "rocket", "gains", "undervalued", "sleeping",
    "next", "big", "early", "massive", "huge", "love", "fire", "letsgo",
}
NEGATIVE_WORDS = {
    "rug", "scam", "dump", "honeypot", "rugpull", "dead", "sell",
    "shitcoin", "ponzi", "fraud", "stolen", "exit", "rekt", "loss",
    "crash", "collapse", "avoid", "warning", "beware", "fake",
}

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

    async def _get_twitter_tweets(self, symbol: str, max_results: int = 100) -> list:
        if not self.twitter_enabled:
            return []
        try:
            query = f"${symbol} -is:retweet lang:en"
            url = f"{TWITTER_API_BASE}/tweets/search/recent?query={query}&max_results={min(max_results, 100)}&tweet.fields=public_metrics,author_id,created_at"
            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
                elif resp.status == 429:
                    logger.warning("Twitter API rate limited")
        except Exception as e:
            logger.debug(f"Twitter tweets error: {e}")
        return []

    async def get_twitter_sentiment(self, symbol: str) -> dict:
        result = {"positive": 0, "negative": 0, "neutral": 0, "score": 0.0, "total": 0}
        if not self.twitter_enabled:
            return result
        try:
            tweets = await self._get_twitter_tweets(symbol, max_results=50)
            if not tweets:
                return result

            for tweet in tweets:
                text = (tweet.get("text", "") or "").lower()
                words = set(re.findall(r'\b\w+\b', text))
                pos = len(words & POSITIVE_WORDS)
                neg = len(words & NEGATIVE_WORDS)

                if pos > neg:
                    result["positive"] += 1
                elif neg > pos:
                    result["negative"] += 1
                else:
                    result["neutral"] += 1

            result["total"] = len(tweets)
            if result["total"] > 0:
                result["score"] = round(
                    (result["positive"] - result["negative"]) / result["total"], 3
                )
        except Exception as e:
            logger.debug(f"Twitter sentiment error: {e}")
        return result

    async def get_influencer_mentions(self, symbol: str) -> dict:
        result = {"count": 0, "names": [], "combined_reach": 0}
        if not self.twitter_enabled:
            return result
        try:
            tweets = await self._get_twitter_tweets(symbol, max_results=100)
            if not tweets:
                return result

            author_ids = list(set(t.get("author_id", "") for t in tweets if t.get("author_id")))
            if not author_ids:
                return result

            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            batch_size = 100
            for i in range(0, len(author_ids), batch_size):
                batch = author_ids[i:i+batch_size]
                ids_param = ",".join(batch)
                url = f"{TWITTER_API_BASE}/users?ids={ids_param}&user.fields=public_metrics,name,username"
                try:
                    async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for user in data.get("data", []):
                                metrics = user.get("public_metrics", {})
                                followers = metrics.get("followers_count", 0)
                                if followers >= 10000:
                                    result["count"] += 1
                                    result["names"].append(f"@{user.get('username', '?')}")
                                    result["combined_reach"] += followers
                except Exception:
                    continue
                await asyncio.sleep(0.5)

            result["names"] = result["names"][:5]
        except Exception as e:
            logger.debug(f"Influencer mention error: {e}")
        return result

    async def detect_viral_trend(self, symbol: str) -> dict:
        result = {"is_viral": False, "mention_rate": 0.0, "trend_strength": 0.0}
        if not self.twitter_enabled:
            return result
        try:
            tweets = await self._get_twitter_tweets(symbol, max_results=100)
            if not tweets:
                return result

            now_ts = asyncio.get_event_loop().time()
            recent_1h = 0
            for tweet in tweets:
                created = tweet.get("created_at", "")
                if created:
                    recent_1h += 1

            result["mention_rate"] = recent_1h
            if recent_1h > 20:
                result["is_viral"] = True
                result["trend_strength"] = min(1.0, recent_1h / 100)
            elif recent_1h > 10:
                result["trend_strength"] = min(0.7, recent_1h / 50)
        except Exception as e:
            logger.debug(f"Viral trend error: {e}")
        return result

    async def calculate_social_score(self, token_address: str, symbol: str) -> tuple:
        result = {
            "score": 0.0,
            "twitter_mentions": 0,
            "sentiment_score": 0.0,
            "influencer_count": 0,
            "is_viral": False,
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

                sentiment = await self.get_twitter_sentiment(symbol)
                result["sentiment_score"] = sentiment.get("score", 0)
                if sentiment.get("score", 0) > 0.5:
                    score += 0.15
                elif sentiment.get("score", 0) > 0.2:
                    score += 0.1
                elif sentiment.get("score", 0) < -0.3:
                    score -= 0.1

                influencers = await self.get_influencer_mentions(symbol)
                result["influencer_count"] = influencers.get("count", 0)
                if influencers.get("count", 0) > 0:
                    score += min(0.2, influencers["count"] * 0.1)

                viral = await self.detect_viral_trend(symbol)
                result["is_viral"] = viral.get("is_viral", False)
                if viral.get("is_viral"):
                    score += 0.25
                elif viral.get("trend_strength", 0) > 0.3:
                    score += 0.1

            result["score"] = max(0.0, min(1.0, score))
            result["details"] = (
                f"Twitter:{result['twitter_mentions']} "
                f"Sent:{result['sentiment_score']:.2f} "
                f"Inf:{result['influencer_count']} "
                f"{'🔥VIRAL' if result['is_viral'] else ''} "
                f"TG:{'Y' if result['has_telegram'] else 'N'} "
                f"Web:{'Y' if result['has_website'] else 'N'} "
                f"Boost:{boost_count}"
            )
        except Exception as e:
            logger.error(f"Social score calc error: {e}")

        return result["score"], result
