import json
import os
import logging
import time
import numpy as np
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from scipy import stats

logger = logging.getLogger("chain_reaction")

CHAIN_REACTION_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "chain_reactions.json")
COIN_COVARIANCE_FILE = os.path.join(os.path.dirname(__file__), "data", "coin_covariance.json")
SECTOR_MAPPING_FILE = os.path.join(os.path.dirname(__file__), "data", "sector_mapping.json")

# Common meme coin sectors based on token names and descriptions
MEME_COIN_SECTORS = {
    'dog': 'dog_meme',
    'cat': 'cat_meme',
    'bird': 'bird_meme',
    'fish': 'sea_meme',
    'shark': 'sea_meme',
    'ape': 'nft_meme',
    'panda': 'animal_meme',
    'coin': 'general_meme',
    'token': 'general_meme',
    'shiba': 'dog_meme',
    'doge': 'dog_meme',
    'pepe': 'viral_meme',
    'binance': 'exchange_meme',
    'solana': 'ecosystem_meme',
    'bitcoin': 'layer_one_meme',
    'ethereum': 'layer_one_meme',
}

# Correlated token patterns (tokens that tend to move together)
TOKEN_CORRELATIONS = [
    ('dogecoin', 'shiba', 'pepe'),  # Dog meme coins
    ('solana', 'sol', 'galaxy'),  # Solana ecosystem
    ('bitcoin', 'btc'),  # Bitcoin ecosystem
    ('ethereum', 'eth'),  # Ethereum ecosystem
    ('meme', 'memecoin', 'dogecoin'),  # General meme coins
]

# Pump patterns to monitor
PUMP_PATTERNS = [
    'early_buying',  # Early stage accumulation
    'volume_surge',  # Sudden volume increase
    'price_explosion',  # Rapid price increase
    'social_momentum',  # Social media momentum
    'whale_activity',  # Large wallet movements
    'cross_chain',  # Cross-chain activity
    'partnership',  # Partnership announcements
]


@dataclass
class TokenCorrelation:
    source_token: str
    target_token: str
    correlation_coefficient: float
    lag_hours: float
    strength: str  # 'weak', 'moderate', 'strong', 'very_strong'
    reliability: float  # 0-1, how many samples support this
    sample_count: int


@dataclass
class ChainReactionEvent:
    event_id: str
    trigger_token: str
    affected_tokens: List[str]
    event_type: str  # 'correlation', 'sector', 'pump', 'dump'
    strength: float
    timestamp: float
    price_change_pct: Dict[str, float]
    volume_change_pct: Dict[str, float]
    confidence: float
    description: str


@dataclass
class SectorAnalysis:
    sector: str
    tokens: List[str]
    sector_correlation: float
    sector_volatility: float
    sector_momentum: float
    sector_sentiment: float
    predictive_power: float


class ChainReactionAnalyzer:
    def __init__(self):
        self.correlations: Dict[str, List[TokenCorrelation]] = defaultdict(list)
        self.events: List[ChainReactionEvent] = []
        self.sectors: Dict[str, List[str]] = defaultdict(list)
        self.token_to_sectors: Dict[str, List[str]] = defaultdict(list)
        self.price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self.last_analysis_time: float = 0

        self._load()
        self._categorize_tokens_to_sectors()

    def _load(self):
        try:
            if os.path.exists(CHAIN_REACTION_DATA_FILE):
                with open(CHAIN_REACTION_DATA_FILE, "r") as f:
                    data = json.load(f)
                    self.correlations = defaultdict(list, {
                        token: [TokenCorrelation(**tc) for tc in corrs]
                        for token, corrs in data.get("correlations", {}).items()
                    })
                    self.events = [ChainReactionEvent(**e) for e in data.get("events", [])]

            if os.path.exists(CHAIN_REACTION_DATA_FILE):
                with open(CHAIN_REACTION_DATA_FILE, "r") as f:
                    cov_data = json.load(f)
                    self.price_history = {
                        token: deque(prices, maxlen=100)
                        for token, prices in cov_data.get("price_history", {}).items()
                    }

            logger.info(f"Chain reaction analyzer loaded: {len(self.correlations)} correlations")
        except Exception as e:
            logger.error(f"Error loading chain reaction analyzer: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(CHAIN_REACTION_DATA_FILE), exist_ok=True)
            data = {
                "correlations": {
                    token: [asdict(tc) for tc in corrs]
                    for token, corrs in self.correlations.items()
                },
                "events": [asdict(e) for e in self.events],
                "saved_at": time.time(),
            }
            with open(CHAIN_REACTION_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving chain reaction analyzer: {e}")

    def _categorize_tokens_to_sectors(self):
        for token_name in MEME_COIN_SECTORS:
            sector = MEME_COIN_SECTORS[token_name]
            self.sectors[sector].append(token_name)
            self.token_to_sectors[token_name].append(sector)

    def update_price_history(self, token: str, price: float, timestamp: float = None):
        ts = timestamp or time.time()
        if price > 0:
            self.price_history[token].append((ts, price))

    def calculate_correlation(self, source_token: str, target_token: str, max_lag_hours: float = 24) -> Optional[TokenCorrelation]:
        source_prices = self.price_history.get(source_token, deque(maxlen=100))
        target_prices = self.price_history.get(target_token, deque(maxlen=100))

        if len(source_prices) < 10 or len(target_prices) < 10:
            return None

        # Align timestamps
        source_data = list(source_prices)
        target_data = list(target_prices)

        if len(source_data) != len(target_data):
            min_len = min(len(source_data), len(target_data))
            source_data = source_data[-min_len:]
            target_data = target_data[-min_len:]

        if len(source_data) < 10:
            return None

        # Calculate correlation with different lags
        best_correlation = None
        best_lag = 0

        for lag in range(0, int(max_lag_hours * 3600), 3600):  # 1-hour steps
            if lag >= len(source_data):
                break

            source_sub = [p[1] for p in source_data[:-lag]] if lag > 0 else [p[1] for p in source_data]
            target_sub = [p[1] for p in target_data[lag:]] if lag > 0 else [p[1] for p in target_data]

            if len(source_sub) < 10:
                continue

            correlation = np.corrcoef(source_sub, target_sub)[0, 1]
            if np.isnan(correlation):
                continue

            if best_correlation is None or abs(correlation) > abs(best_correlation):
                best_correlation = correlation
                best_lag = lag / 3600

        if best_correlation is None:
            return None

        # Determine strength
        strength = "weak"
        if abs(best_correlation) >= 0.8:
            strength = "very_strong"
        elif abs(best_correlation) >= 0.6:
            strength = "strong"
        elif abs(best_correlation) >= 0.4:
            strength = "moderate"

        # Check if correlation is reliable (based on sample size)
        reliability = min(len(source_data) / 50, 1.0)

        return TokenCorrelation(
            source_token=source_token,
            target_token=target_token,
            correlation_coefficient=best_correlation,
            lag_hours=best_lag,
            strength=strength,
            reliability=reliability,
            sample_count=len(source_data),
        )

    def analyze_sector_correlation(self, sector: str) -> Optional[SectorAnalysis]:
        tokens = self.sectors.get(sector, [])
        if len(tokens) < 2:
            return None

        # Calculate average correlation within sector
        correlations = []
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                corr = self._calculate_pair_correlation(tokens[i], tokens[j])
                if corr is not None:
                    correlations.append(corr)

        if not correlations:
            return None

        avg_correlation = sum(correlations) / len(correlations)

        # Calculate sector metrics
        sector_volatility = self._calculate_sector_volatility(sector)
        sector_momentum = self._calculate_sector_momentum(sector)

        # Get sentiment from social (if available)
        sector_sentiment = self._get_sector_sentiment(sector)

        # Predictive power based on correlation strength and reliability
        predictive_power = min(abs(avg_correlation) * 0.8, 1.0) * self._get_sector_reliability(sector)

        return SectorAnalysis(
            sector=sector,
            tokens=tokens,
            sector_correlation=avg_correlation,
            sector_volatility=sector_volatility,
            sector_momentum=sector_momentum,
            sector_sentiment=sector_sentiment,
            predictive_power=predictive_power,
        )

    def _calculate_pair_correlation(self, token1: str, token2: str) -> Optional[float]:
        prices1 = [p[1] for p in self.price_history.get(token1, deque(maxlen=100))]
        prices2 = [p[1] for p in self.price_history.get(token2, deque(maxlen=100))]

        if len(prices1) < 10 or len(prices2) < 10:
            return None

        correlation = np.corrcoef(prices1, prices2)[0, 1]
        return correlation if not np.isnan(correlation) else None

    def _calculate_sector_volatility(self, sector: str) -> float:
        tokens = self.sectors.get(sector, [])
        if not tokens:
            return 0.0

        volatilities = []
        for token in tokens:
            prices = [p[1] for p in self.price_history.get(token, deque(maxlen=100))]
            if len(prices) >= 10:
                returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
                if returns:
                    volatilities.append(np.std(returns))

        return np.mean(volatilities) if volatilities else 0.0

    def _calculate_sector_momentum(self, sector: str) -> float:
        tokens = self.sectors.get(sector, [])
        if not tokens:
            return 0.0

        momenta = []
        now = time.time()
        cutoff = now - (24 * 3600)

        for token in tokens:
            history = [p for p in self.price_history.get(token, deque(maxlen=100)) if p[0] >= cutoff]
            if len(history) >= 10:
                recent_prices = [p[1] for p in history[-10:]]
                price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
                momenta.append(price_change)

        return np.mean(momenta) if momenta else 0.0

    def _get_sector_sentiment(self, sector: str) -> float:
        # This would integrate with social sentiment analysis
        # For now, return a placeholder based on token correlation
        tokens = self.sectors.get(sector, [])
        if not tokens:
            return 0.0

        # Check if tokens are in correlated groups
        correlated_count = 0
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                corr = self._calculate_pair_correlation(tokens[i], tokens[j])
                if corr and abs(corr) > 0.5:
                    correlated_count += 1

        return min(correlated_count / (len(tokens) * (len(tokens) - 1) / 2 * 2), 1.0)

    def _get_sector_reliability(self, sector: str) -> float:
        # Calculate how reliable the sector's patterns are
        tokens = self.sectors.get(sector, [])
        if not tokens:
            return 0.0

        total_reliability = 0.0
        for token in tokens:
            correlations = self.correlations.get(token, [])
            if correlations:
                avg_reliability = sum(c.reliability for c in correlations) / len(correlations)
                total_reliability += avg_reliability

        return total_reliability / len(tokens) if tokens else 0.0

    def detect_chain_reactions(self) -> List[ChainReactionEvent]:
        events = []
        now = time.time()

        # Check for sector-wide movements
        for sector, tokens in self.sectors.items():
            if len(tokens) < 2:
                continue

            sector_analysis = self.analyze_sector_correlation(sector)
            if not sector_analysis or sector_analysis.sector_correlation < 0.3:
                continue

            # Calculate price changes for sector tokens
            price_changes = {}
            volume_changes = {}

            for token in tokens:
                history = self.price_history.get(token, deque(maxlen=100))
                if len(history) >= 10:
                    recent_price = history[-1][1]
                    earlier_price = history[-10][1]
                    price_change = (recent_price - earlier_price) / earlier_price * 100

                    # Find volume change (approximate from price changes)
                    volume_change = sector_analysis.sector_momentum * (1 if price_change > 0 else -1)

                    price_changes[token] = price_change
                    volume_changes[token] = volume_change

            if any(abs(change) > 5 for change in price_changes.values()):
                event = self._create_sector_event(
                    sector, "sector_movement", price_changes, volume_changes,
                    sector_analysis.sector_correlation, now,
                    f"Sector {sector} showing correlated movement"
                )
                events.append(event)

        # Check for token-to-token correlations
        for token1 in self.price_history.keys():
            for token2 in self.price_history.keys():
                if token1 == token2 or token2 not in self.price_history:
                    continue

                correlation = self._calculate_pair_correlation(token1, token2)
                if correlation and abs(correlation) > 0.6:
                    # Check if both tokens have recent significant movements
                    price_change1 = self._get_recent_price_change(token1)
                    price_change2 = self._get_recent_price_change(token2)

                    if abs(price_change1) > 10 and abs(price_change2) > 10:
                        event = self._create_correlation_event(
                            token1, token2, correlation, price_change1, price_change2,
                            now, f"Strong correlation between {token1} and {token2}"
                        )
                        events.append(event)

        # Detect pump patterns
        for token in self.price_history.keys():
            if self._detect_pump_pattern(token):
                event = self._create_pump_event(
                    token, now, f"Pump pattern detected for {token}"
                )
                events.append(event)

        self.events.extend(events)
        self._save()

        return events

    def _create_sector_event(self, sector: str, event_type: str,
                           price_changes: Dict[str, float],
                           volume_changes: Dict[str, float],
                           correlation: float, timestamp: float,
                           description: str) -> ChainReactionEvent:
        affected_tokens = list(price_changes.keys())

        # Calculate event strength based on correlation and consistency
        strength = min(abs(correlation) * 0.8, 1.0)

        return ChainReactionEvent(
            event_id=f"sector_{sector}_{int(timestamp)}",
            trigger_token=sector,
            affected_tokens=affected_tokens,
            event_type=event_type,
            strength=strength,
            timestamp=timestamp,
            price_change_pct=price_changes,
            volume_change_pct=volume_changes,
            confidence=strength,
            description=description,
        )

    def _create_correlation_event(self, token1: str, token2: str,
                                 correlation: float, price_change1: float,
                                 price_change2: float, timestamp: float,
                                 description: str) -> ChainReactionEvent:
        strength = min(abs(correlation) * 0.8, 1.0)

        return ChainReactionEvent(
            event_id=f"corr_{token1}_{token2}_{int(timestamp)}",
            trigger_token=token1,
            affected_tokens=[token2],
            event_type="correlation",
            strength=strength,
            timestamp=timestamp,
            price_change_pct={token1: price_change1, token2: price_change2},
            volume_change_pct={},
            confidence=strength,
            description=description,
        )

    def _create_pump_event(self, token: str, timestamp: float,
                          description: str) -> ChainReactionEvent:
        price_change = self._get_recent_price_change(token)

        return ChainReactionEvent(
            event_id=f"pump_{token}_{int(timestamp)}",
            trigger_token=token,
            affected_tokens=[],
            event_type="pump",
            strength=min(abs(price_change) / 50, 1.0),
            timestamp=timestamp,
            price_change_pct={token: price_change},
            volume_change_pct={},
            confidence=min(abs(price_change) / 100, 1.0),
            description=description,
        )

    def _get_recent_price_change(self, token: str, hours: float = 24) -> float:
        history = self.price_history.get(token, deque(maxlen=100))
        if len(history) < 10:
            return 0.0

        cutoff = time.time() - (hours * 3600)
        recent_history = [p for p in history if p[0] >= cutoff]

        if len(recent_history) < 2:
            return 0.0

        start_price = recent_history[0][1]
        end_price = recent_history[-1][1]

        return (end_price - start_price) / start_price * 100

    def _detect_pump_pattern(self, token: str) -> bool:
        history = self.price_history.get(token, deque(maxlen=100))
        if len(history) < 20:
            return False

        # Check for rapid price increase over short period
        recent_history = history[-20:]
        prices = [p[1] for p in recent_history]

        # Calculate if price increased by more than 50% in 20 data points
        price_change = (prices[-1] - prices[0]) / prices[0] * 100
        if price_change < 50:
            return False

        # Check for volume spike
        volume_change = self._estimate_volume_change(token, recent_history)
        if volume_change < 100:
            return False

        return True

    def _estimate_volume_change(self, token: str, price_history_data) -> float:
        # This is a rough estimate based on price movements
        if len(price_history_data) < 5:
            return 0.0

        # Simple volume proxy: higher volatility = higher volume
        prices = [p[1] for p in price_history_data]
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]

        return np.mean([abs(r) for r in returns]) * 1000

    def get_sector_alerts(self) -> List[dict]:
        alerts = []
        for sector, analysis in self._get_all_sector_analyses().items():
            if analysis.sector_correlation > 0.7 and analysis.sector_momentum > 0.1:
                alerts.append({
                    "sector": sector,
                    "alert_type": "strong_correlation",
                    "correlation": analysis.sector_correlation,
                    "momentum": analysis.sector_momentum,
                    "sentiment": analysis.sector_sentiment,
                    "predictive_power": analysis.predictive_power,
                    "message": f"Sector {sector} showing strong correlated movement",
                })
            elif analysis.sector_correlation < -0.5 and analysis.sector_momentum < -0.1:
                alerts.append({
                    "sector": sector,
                    "alert_type": "inverse_correlation",
                    "correlation": analysis.sector_correlation,
                    "momentum": analysis.sector_momentum,
                    "sentiment": analysis.sector_sentiment,
                    "predictive_power": analysis.predictive_power,
                    "message": f"Sector {sector} showing inverse correlated movement",
                })

        return alerts

    def _get_all_sector_analyses(self) -> Dict[str, SectorAnalysis]:
        analyses = {}
        for sector in self.sectors.keys():
            analysis = self.analyze_sector_correlation(sector)
            if analysis:
                analyses[sector] = analysis
        return analyses

    def get_chain_reaction_summary(self) -> dict:
        return {
            "total_events": len(self.events),
            "recent_events": len([e for e in self.events if e.timestamp > time.time() - 3600]),
            "sector_count": len(self.sectors),
            "token_count": len(self.price_history),
            "last_analysis": self.last_analysis_time,
            "strong_events": len([e for e in self.events if e.strength > 0.7]),
            "correlation_events": len([e for e in self.events if e.event_type == "correlation"]),
            "sector_events": len([e for e in self.events if e.event_type == "sector_movement"]),
            "pump_events": len([e for e in self.events if e.event_type == "pump"]),
        }

    def save(self):
        self._save()


sector_analyzer = SectorAnalysis
chain_reaction_analyzer = ChainReactionAnalyzer()
