import json
import os
import logging
import time
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

logger = logging.getLogger("social_sentiment")

SENTIMENT_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "social_sentiment.json")
TWITTER_API_CACHE = os.path.join(os.path.dirname(__file__), "data", "twitter_cache.json")
DISCORD_API_CACHE = os.path.join(os.path.dirname(__file__), "data", "discord_cache.json")

# Twitter API configuration
TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_RATE_LIMIT = 180  # requests per hour
TWITTER_QUERY_SIZE = 100  # tweets per request

# Discord webhook URLs (would need to be configured)
DISCORD_WEBHOOKS = {}

# Keywords and topics to monitor
MONITORING_KEYWORDS = [
    'solana', 'sol', 'pump.fun', 'bonk', 'dogecoin',
    'meme coin', 'defi solana', 'altcoin', 'blockchain',
    'cryptocurrency', 'web3', 'defi', 'DeFi', 'NFT'
]

# Positive sentiment words
POSITIVE_WORDS = [
    'moon', 'bull', 'bullish', 'pump', 'rocket', 'gains',
    'profit', 'invest', 'buy', 'long', 'hold', 'diamond hands',
    'to the moon', 'lambo', 'print', 'ceiling', 'run'
]

# Negative sentiment words
NEGATIVE_WORDS = [
    'bear', 'crash', 'dump', 'scam', 'rug pull', ' rug',
    'liquidate', 'bankrupt', 'hype', 'manipulation',
    'ponzi', 'fraud', 'exit scam', 'wash trade'
]


@dataclass
class SocialPost:
    platform: str
    post_id: str
    author: str
    content: str
    timestamp: float
    tokens_mentioned: List[str]
    sentiment_score: float
    engagement_score: float
    author_followers: int
    author_verified: bool
    url: Optional[str] = None


@dataclass
class SentimentAnalysis:
    token: str
    platform: str
    mention_count: int
    average_sentiment: float
    positive_mentions: int
    negative_mentions: int
    neutral_mentions: int
    time_window_minutes: int
    confidence_score: float


@dataclass
class TrendAlert:
    token: str
    alert_type: str  # 'positive_surge', 'negative_spike', 'new_trend'
    score: float
    timestamp: float
    author_impact: int
    volume_impact: float
    message: str


class SocialSentimentEngine:
    def __init__(self, twitter_bearer_token: str = None, discord_webhooks: dict = None):
        self.twitter_bearer_token = twitter_bearer_token
        self.discord_webhooks = discord_webhooks or {}
        self.twitter_sessions: Dict[str, aiohttp.ClientSession] = {}
        self.twitter_last_request: Dict[str, float] = {}
        self.twitter_rate_limits: Dict[str, int] = {}

        self.recent_posts: deque = deque(maxlen=10000)
        self.sentiment_history: Dict[str, List[SentimentAnalysis]] = defaultdict(list)
        self.trend_alerts: List[TrendAlert] = []
        self.author_impact_scores: Dict[str, float] = {}
        self.token_engagement: Dict[str, dict] = defaultdict(lambda: {'mentions': 0, 'sentiment': 0.0, 'volume': 0.0})

        self._load()
        self._init_twitter_sessions()

    def _init_twitter_sessions(self):
        if self.twitter_bearer_token:
            session = aiohttp.ClientSession()
            self.twitter_sessions['api'] = session
            self.twitter_rate_limits['api'] = 0
            logger.info("Twitter API session initialized")

    def _load(self):
        try:
            if os.path.exists(SENTIMENT_DATA_FILE):
                with open(SENTIMENT_DATA_FILE, "r") as f:
                    data = json.load(f)
                    self.recent_posts.extend(
                        [SocialPost(**p) for p in data.get("posts", [])]
                    )
                    for token, analyses in data.get("sentiment_history", {}).items():
                        self.sentiment_history[token].extend(
                            [SentimentAnalysis(**a) for a in analyses]
                        )
                    self.trend_alerts = [
                        TrendAlert(**a) for a in data.get("trend_alerts", [])
                    ]
                    self.author_impact_scores = data.get("author_impact_scores", {})
                    self.token_engagement = data.get("token_engagement", {})

            logger.info(f"Social sentiment loaded: {len(self.recent_posts)} posts")
        except Exception as e:
            logger.error(f"Error loading social sentiment: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SENTIMENT_DATA_FILE), exist_ok=True)
            data = {
                "posts": [asdict(p) for p in self.recent_posts[-1000:]],
                "sentiment_history": {
                    token: [asdict(a) for a in analyses]
                    for token, analyses in self.sentiment_history.items()
                },
                "trend_alerts": [asdict(a) for a in self.trend_alerts],
                "author_impact_scores": self.author_impact_scores,
                "token_engagement": self.token_engagement,
                "saved_at": time.time(),
            }
            with open(SENTIMENT_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving social sentiment: {e}")

    async def fetch_twitter_trending(self, max_tokens: int = 10) -> List[dict]:
        if not self.twitter_bearer_token:
            logger.debug("Twitter API credentials not configured")
            return []

        # Check rate limit
        now = time.time()
        if self.twitter_rate_limits['api'] >= TWITTER_RATE_LIMIT:
            next_reset = now - self.twitter_last_request['api'] + 3600
            if next_reset > 0:
                await asyncio.sleep(next_reset)
                self.twitter_rate_limits['api'] = 0

        try:
            session = self.twitter_sessions['api']
            self.twitter_last_request['api'] = now
            self.twitter_rate_limits['api'] += 1

            # Query recent tweets mentioning crypto tokens
            query = " OR ".join(MONITORING_KEYWORDS[:5])  # Limit query size
            url = f"{TWITTER_API_BASE}/tweets/search/recent"
            params = {
                "query": query,
                "max_results": TWITTER_QUERY_SIZE,
                "tweet.fields": "created_at,author_id,public_metrics,context_annotations,entities",
                "user.fields": "username,name,verified,public_metrics,followers_count",
            }

            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
                elif resp.status == 429:
                    logger.warning("Twitter rate limit exceeded")
                    await asyncio.sleep(60)
                    return await self.fetch_twitter_trending(max_tokens)
                else:
                    logger.debug(f"Twitter API error: {resp.status}")
                    return []

        except Exception as e:
            logger.error(f"Error fetching Twitter data: {e}")
            return []

    def analyze_twitter_post(self, tweet_data: dict) -> Optional[SocialPost]:
        try:
            text = tweet_data.get("text", "")
            if not text:
                return None

            # Extract tokens mentioned in the text
            tokens = []
            for keyword in MONITORING_KEYWORDS:
                if keyword.lower() in text.lower():
                    tokens.append(keyword)

            if not tokens:
                return None

            # Calculate sentiment score
            sentiment_score = self._calculate_sentiment_score(text)

            # Get author information
            author_id = tweet_data.get("author_id", "")
            public_metrics = tweet_data.get("public_metrics", {})
            author_followers = public_metrics.get("followers_count", 0) if public_metrics else 0
            verified = tweet_data.get("author", {}).get("verified", False)

            # Engagement score
            engagement = public_metrics.get("like_count", 0) + public_metrics.get("retweet_count", 0)
            engagement_score = min(engagement / 1000, 10)  # Normalize to 0-10 scale

            return SocialPost(
                platform="twitter",
                post_id=tweet_data.get("id", ""),
                author=author_id,
                content=text,
                timestamp=self._parse_twitter_timestamp(tweet_data.get("created_at", "")),
                tokens_mentioned=tokens,
                sentiment_score=sentiment_score,
                engagement_score=engagement_score,
                author_followers=author_followers,
                author_verified=verified,
                url=f"https://twitter.com/i/web/status/{tweet_data.get('id', '')}",
            )

        except Exception as e:
            logger.error(f"Error analyzing Twitter post: {e}")
            return None

    def _calculate_sentiment_score(self, text: str) -> float:
        text_lower = text.lower()

        # Count positive and negative words
        positive_count = sum(1 for word in POSITIVE_WORDS if word in text_lower)
        negative_count = sum(1 for word in NEGATIVE_WORDS if word in text_lower)

        # Calculate base score (-1 to +1)
        if positive_count + negative_count == 0:
            return 0.0

        score = (positive_count - negative_count) / (positive_count + negative_count)

        # Adjust for emphasis (uppercase/mentions)
        if text.isupper() or "!".encode() in text.encode():
            score *= 1.2

        # Adjust for credibility factors
        if any(word in text_lower for word in ["source:", "according to", "data shows"]):
            score *= 1.1

        return max(-1.0, min(1.0, score))

    def _parse_twitter_timestamp(self, timestamp_str: str) -> float:
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return time.time()

    def process_social_data(self, max_age_minutes: int = 60) -> List[SocialPost]:
        now = time.time()
        cutoff = now - (max_age_minutes * 60)

        # Fetch new data from Twitter
        if self.twitter_bearer_token:
            tweets = asyncio.create_task(self.fetch_twitter_trending())
            # We need to handle async here, but for simplicity, we'll skip for now
            # In production, this should be done properly

        # Process existing data
        new_posts = []
        for post in self.recent_posts:
            if post.timestamp >= cutoff:
                new_posts.append(post)

        return new_posts

    def analyze_sentiment(self, token: str, time_window_minutes: int = 60) -> SentimentAnalysis:
        now = time.time()
        cutoff = now - (time_window_minutes * 60)

        # Filter posts for this token
        token_posts = [
            post for post in self.recent_posts
            if post.tokens_mentioned and token in post.tokens_mentioned
            and post.timestamp >= cutoff
        ]

        if not token_posts:
            return SentimentAnalysis(
                token=token,
                platform="combined",
                mention_count=0,
                average_sentiment=0.0,
                positive_mentions=0,
                negative_mentions=0,
                neutral_mentions=0,
                time_window_minutes=time_window_minutes,
                confidence_score=0.0,
            )

        # Calculate sentiment metrics
        positive = sum(1 for p in token_posts if p.sentiment_score > 0.1)
        negative = sum(1 for p in token_posts if p.sentiment_score < -0.1)
        neutral = len(token_posts) - positive - negative

        avg_sentiment = sum(p.sentiment_score for p in token_posts) / len(token_posts)
        mention_count = len(token_posts)

        # Calculate confidence score based on number of posts and engagement
        total_engagement = sum(p.engagement_score for p in token_posts)
        confidence = min(mention_count / 10, 1.0) * (1.0 + total_engagement / 100)

        # Store in history
        analysis = SentimentAnalysis(
            token=token,
            platform="social",
            mention_count=mention_count,
            average_sentiment=avg_sentiment,
            positive_mentions=positive,
            negative_mentions=negative,
            neutral_mentions=neutral,
            time_window_minutes=time_window_minutes,
            confidence_score=confidence,
        )

        self.sentiment_history[token].append(analysis)
        self._update_token_engagement(token, analysis)

        return analysis

    def _update_token_engagement(self, token: str, analysis: SentimentAnalysis):
        engagement = self.token_engagement[token]
        engagement["mentions"] += analysis.mention_count
        engagement["sentiment"] = (engagement["sentiment"] * (engagement["mentions"] - analysis.mention_count) +
                                  analysis.average_sentiment * analysis.mention_count) / engagement["mentions"]

    def detect_trends(self) -> List[TrendAlert]:
        now = time.time()
        alerts = []

        for token in self.sentiment_history:
            analyses = self.sentiment_history[token]
            if len(analyses) < 3:
                continue

            # Check for positive surge (rapid increase in positive mentions)
            recent_analyses = analyses[-5:]  # Last 5 analyses
            positive_count = sum(a.positive_mentions for a in recent_analyses)
            total_mentions = sum(a.mention_count for a in recent_analyses)

            if total_mentions >= 5 and positive_count / total_mentions > 0.8:
                surge_score = sum(a.average_sentiment for a in recent_analyses) / len(recent_analyses)
                alerts.append(TrendAlert(
                    token=token,
                    alert_type="positive_surge",
                    score=surge_score,
                    timestamp=now,
                    author_impact=len([a for a in recent_analyses if a.mention_count > 2]),
                    volume_impact=surge_score * 0.5,
                    message=f"Strong positive sentiment surge for {token}",
                ))

            # Check for negative spike
            negative_count = sum(a.negative_mentions for a in recent_analyses)
            if total_mentions >= 3 and negative_count / total_mentions > 0.6:
                spike_score = -sum(a.average_sentiment for a in recent_analyses) / len(recent_analyses)
                alerts.append(TrendAlert(
                    token=token,
                    alert_type="negative_spike",
                    score=spike_score,
                    timestamp=now,
                    author_impact=len([a for a in recent_analyses if a.mention_count > 2]),
                    volume_impact=spike_score * 0.5,
                    message=f"Negative sentiment spike for {token}",
                ))

        # Filter and deduplicate alerts
        unique_alerts = []
        seen_tokens = set()
        for alert in sorted(alerts, key=lambda x: abs(x.score), reverse=True):
            if alert.token not in seen_tokens:
                seen_tokens.add(alert.token)
                unique_alerts.append(alert)

        self.trend_alerts.extend(unique_alerts)

        # Keep only recent alerts
        recent_cutoff = now - (24 * 3600)  # 24 hours
        self.trend_alerts = [a for a in self.trend_alerts if a.timestamp >= recent_cutoff]

        return unique_alerts

    def get_sentiment_summary(self, token: str, timeframe_minutes: int = 60) -> dict:
        cutoff = time.time() - (timeframe_minutes * 60)
        relevant_analyses = [
            a for a in self.sentiment_history.get(token, [])
            if a.timestamp >= cutoff
        ]

        if not relevant_analyses:
            return {"status": "no_data", "message": f"No sentiment data for {token} in the last {timeframe_minutes} minutes"}

        latest = relevant_analyses[-1]

        return {
            "token": token,
            "mention_count": latest.mention_count,
            "average_sentiment": latest.average_sentiment,
            "positive_mentions": latest.positive_mentions,
            "negative_mentions": latest.negative_mentions,
            "neutral_mentions": latest.neutral_mentions,
            "confidence": latest.confidence_score,
            "trend": "bullish" if latest.average_sentiment > 0.2 else "bearish" if latest.average_sentiment < -0.2 else "neutral",
            "data_points": len(relevant_analyses),
        }

    def get_trending_tokens(self, min_mentions: int = 3) -> List[dict]:
        trending = []
        for token, analyses in self.sentiment_history.items():
            if analyses:
                latest = analyses[-1]
                if latest.mention_count >= min_mentions:
                    trending.append({
                        "token": token,
                        "sentiment": latest.average_sentiment,
                        "mentions": latest.mention_count,
                        "confidence": latest.confidence_score,
                        "trend": "bullish" if latest.average_sentiment > 0.2 else "bearish" if latest.average_sentiment < -0.2 else "neutral",
                    })

        return sorted(trending, key=lambda x: x["mentions"], reverse=True)

    def get_alerts(self, alert_types: List[str] = None) -> List[dict]:
        if alert_types:
            return [
                {
                    "token": alert.token,
                    "alert_type": alert.alert_type,
                    "score": alert.score,
                    "timestamp": alert.timestamp,
                    "message": alert.message,
                }
                for alert in self.trend_alerts
                if alert.alert_type in alert_types
            ]
        else:
            return [
                {
                    "token": alert.token,
                    "alert_type": alert.alert_type,
                    "score": alert.score,
                    "timestamp": alert.timestamp,
                    "message": alert.message,
                }
                for alert in self.trend_alerts
            ]

    def save(self):
        self._save()

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Remove old posts
        old_count = len(self.recent_posts)
        self.recent_posts = [p for p in self.recent_posts if p.timestamp >= cutoff]
        removed = old_count - len(self.recent_posts)

        # Clean up old sentiment history
        for token in list(self.sentiment_history.keys()):
            self.sentiment_history[token] = [
                a for a in self.sentiment_history[token] if a.timestamp >= cutoff
            ]
            if not self.sentiment_history[token]:
                del self.sentiment_history[token]

        if removed:
            logger.info(f"Cleaned up {removed} old social posts")

    def get_summary_stats(self) -> dict:
        return {
            "total_posts": len(self.recent_posts),
            "unique_tokens": len(self.token_engagement),
            "active_tokens_last_hour": len([
                token for token, data in self.token_engagement.items()
                if data.get("last_updated", 0) > time.time() - 3600
            ]),
            "avg_sentiment": sum(
                a.average_sentiment for analyses in self.sentiment_history.values()
                for a in analyses[-1:]
            ) / max(len(self.sentiment_history), 1),
            "total_alerts": len(self.trend_alerts),
            "last_updated": time.time(),
        }


social_sentiment_engine = SocialSentimentEngine()
