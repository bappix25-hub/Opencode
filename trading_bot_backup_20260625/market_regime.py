import json
import os
import logging
import time
import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

logger = logging.getLogger("market_regime")

REGIME_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "market_regimes.json")
GLOBAL_MARKET_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "global_market_data.json")
REGIME_TRANSITION_FILE = os.path.join(os.path.dirname(__file__), "data", "regime_transitions.json")

# Market regime definitions
REGIME_TYPES = {
    'bull_market': {
        'description': 'Strong upward trend, high volatility, positive sentiment',
        'min_volatility': 0.3,
        'min_momentum': 0.2,
        'min_sentiment': 0.6,
        'typical_duration': '2-8 weeks',
        'risk_level': 'medium',
        'strategy': 'trend_following, momentum_trading'
    },
    'bear_market': {
        'description': 'Sustained downward trend, low volatility, negative sentiment',
        'max_volatility': 0.5,
        'max_momentum': -0.2,
        'max_sentiment': -0.4,
        'typical_duration': '4-12 weeks',
        'risk_level': 'high',
        'strategy': 'defensive_positioning, short_selling'
    },
    'sideways_market': {
        'description': 'Range-bound trading, low volatility, mixed sentiment',
        'max_volatility': 0.4,
        'min_momentum': -0.1,
        'max_momentum': 0.1,
        'min_sentiment': -0.2,
        'max_sentiment': 0.2,
        'typical_duration': '3-6 weeks',
        'risk_level': 'low',
        'strategy': 'range_trading, options_strategies'
    },
    'volatile_market': {
        'description': 'High volatility, unpredictable movements, mixed signals',
        'min_volatility': 0.5,
        'risk_level': 'very_high',
        'strategy': 'risk_management, volatility_strategies'
    },
    'transition_market': {
        'description': 'Market in transition between regimes, high uncertainty',
        'risk_level': 'high',
        'strategy': 'defensive, wait_for_clarity'
    }
}

# Market indicators
MARKET_INDICATORS = [
    'btc_price_change_24h',
    'eth_price_change_24h',
    'sol_price_change_24h',
    'total_market_volume_24h',
    'market_cap_change_24h',
    'fear_greed_index',
    'funding_rate',
    'liquidation_ratio',
    'active_wallets_24h',
    'new_wallets_24h',
    'social_sentiment_score',
    'keyword_sentiment',
    'viral_trend_score',
]

# Weight of each indicator per regime
REGIME_INDICATOR_WEIGHTS = {
    'bull_market': {
        'btc_price_change_24h': 0.3,
        'eth_price_change_24h': 0.25,
        'total_market_volume_24h': 0.2,
        'social_sentiment_score': 0.2,
        'fear_greed_index': 0.1,
    },
    'bear_market': {
        'btc_price_change_24h': 0.4,
        'eth_price_change_24h': 0.3,
        'liquidation_ratio': 0.2,
        'social_sentiment_score': -0.2,
        'funding_rate': -0.1,
    },
    'sideways_market': {
        'total_market_volume_24h': -0.3,
        'btc_price_change_24h': 0.1,
        'eth_price_change_24h': 0.1,
        'social_sentiment_score': 0.1,
        'fear_greed_index': 0.1,
    },
    'volatile_market': {
        'fear_greed_index': 0.3,
        'funding_rate': 0.3,
        'social_sentiment_score': 0.2,
        'liquidation_ratio': 0.2,
    },
}


@dataclass
class MarketCondition:
    indicator: str
    value: float
    weight: float
    trend: str  # 'increasing', 'decreasing', 'stable'
    momentum: float


@dataclass
class RegimeAnalysis:
    regime: str
    confidence: float
    probability: float
    strength: float
    indicators: Dict[str, MarketCondition]
    transition_probability: Dict[str, float]
    recommended_strategy: str
    risk_assessment: str
    key_factors: List[str]
    time_to_change_estimate: float
    last_updated: float


@dataclass
class RegimeTransition:
    from_regime: str
    to_regime: str
    transition_time: float
    cause: str
    contributing_factors: List[str]
    impact_score: float
    recovery_time_estimate: float


class MarketRegimeDetector:
    def __init__(self):
        self.current_regime: Optional[str] = None
        self.regime_history: List[dict] = []
        self.transitions: List[RegimeTransition] = []
        self.indicator_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.regime_scores: Dict[str, float] = {}
        self.regime_transitions: Dict[str, List[float]] = defaultdict(list)
        self.volatility_regime: Dict[str, dict] = {}
        self.momentum_regime: Dict[str, dict] = {}

        self._load()

    def _load(self):
        try:
            if os.path.exists(REGIME_DATA_FILE):
                with open(REGIME_DATA_FILE, "r") as f:
                    data = json.load(f)
                    self.regime_history = data.get("regime_history", [])
                    self.current_regime = data.get("current_regime")
                    self.transitions = [RegimeTransition(**t) for t in data.get("transitions", [])]

            if os.path.exists(GLOBAL_MARKET_DATA_FILE):
                with open(GLOBAL_MARKET_DATA_FILE, "r") as f:
                    data = json.load(f)
                    self.indicator_history = {
                        indicator: deque(prices, maxlen=1000)
                        for indicator, prices in data.get("indicator_history", {}).items()
                    }

            logger.info(f"Market regime detector loaded: current={self.current_regime}, history={len(self.regime_history)}")
        except Exception as e:
            logger.error(f"Error loading market regime detector: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(REGIME_DATA_FILE), exist_ok=True)
            data = {
                "current_regime": self.current_regime,
                "regime_history": self.regime_history,
                "transitions": [asdict(t) for t in self.transitions],
                "saved_at": time.time(),
            }
            with open(REGIME_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving market regime detector: {e}")

    def update_indicator(self, indicator: str, value: float, timestamp: float = None):
        ts = timestamp or time.time()
        if value != 0:
            self.indicator_history[indicator].append((ts, value))

    def analyze_regime(self, force_recalculate: bool = False) -> RegimeAnalysis:
        now = time.time()

        # Check if we need to recalculate
        if not force_recalculate and self.current_regime:
            last_update = self.regime_history[-1].get("timestamp", 0) if self.regime_history else 0
            if now - last_update < 300:  # Don't recalculate more than once every 5 minutes
                return self._get_cached_regime_analysis()

        # Calculate indicators
        indicators = self._calculate_indicators()

        # Score each regime
        regime_scores = self._score_regimes(indicators)

        # Determine most likely regime
        best_regime = max(regime_scores.items(), key=lambda x: x[1])
        best_score = best_regime[1]

        # Calculate transition probabilities
        transition_probs = self._calculate_transition_probabilities(best_regime[0])

        # Determine strength based on score
        strength = self._calculate_regime_strength(best_regime[0], regime_scores)

        # Get recommended strategy
        recommended_strategy = REGIME_TYPES.get(best_regime[0], {}).get("strategy", "hold")

        # Get risk assessment
        risk_assessment = REGIME_TYPES.get(best_regime[0], {}).get("risk_level", "medium")

        # Get key factors
        key_factors = self._get_key_factors(best_regime[0], indicators)

        # Estimate time to change
        time_to_change = self._estimate_time_to_change(best_regime[0], regime_scores)

        # Create analysis
        analysis = RegimeAnalysis(
            regime=best_regime[0],
            confidence=best_score,
            probability=best_score / sum(regime_scores.values()) if sum(regime_scores.values()) > 0 else 0,
            strength=strength,
            indicators=indicators,
            transition_probability=transition_probs,
            recommended_strategy=recommended_strategy,
            risk_assessment=risk_assessment,
            key_factors=key_factors,
            time_to_change_estimate=time_to_change,
            last_updated=now,
        )

        # Update state
        if self.current_regime != best_regime[0]:
            self._record_regime_transition(self.current_regime, best_regime[0], now, analysis)
            self.current_regime = best_regime[0]

        # Record in history
        self.regime_history.append({
            "regime": best_regime[0],
            "confidence": best_score,
            "timestamp": now,
            "indicators": {k: {'value': v.value, 'weight': v.weight} for k, v in indicators.items()},
        })

        self.regime_scores = regime_scores

        # Save
        self._save()

        return analysis

    def _calculate_indicators(self) -> Dict[str, MarketCondition]:
        indicators = {}
        now = time.time()

        for indicator in MARKET_INDICATORS:
            values = list(self.indicator_history.get(indicator, deque(maxlen=1000)))
            if len(values) < 10:
                continue

            # Get latest value
            latest_value = values[-1][1]

            # Calculate trend
            if len(values) >= 5:
                recent_values = [v[1] for v in values[-5:]]
                old_values = [v[1] for v in values[-10:-5]] if len(values) >= 10 else recent_values

                if len(recent_values) >= 2 and len(old_values) >= 2:
                    recent_change = (recent_values[-1] - recent_values[0]) / recent_values[0] * 100 if recent_values[0] != 0 else 0
                    old_change = (old_values[-1] - old_values[0]) / old_values[0] * 100 if old_values[0] != 0 else 0

                    if recent_change > abs(old_change) * 1.5:
                        trend = "increasing"
                        momentum = (recent_change - old_change) / abs(old_change) if old_change != 0 else 0
                    elif recent_change < -abs(old_change) * 1.5:
                        trend = "decreasing"
                        momentum = (old_change - recent_change) / abs(old_change) if old_change != 0 else 0
                    else:
                        trend = "stable"
                        momentum = 0
                else:
                    trend = "stable"
                    momentum = 0
            else:
                trend = "stable"
                momentum = 0

            # Get weight from appropriate regime
            weight = 0.0
            if self.current_regime:
                weight = REGIME_INDICATOR_WEIGHTS.get(self.current_regime, {}).get(indicator, 0.1)
            else:
                weight = 0.1  # Default weight

            indicators[indicator] = MarketCondition(
                indicator=indicator,
                value=latest_value,
                weight=weight,
                trend=trend,
                momentum=momentum,
            )

        return indicators

    def _score_regimes(self, indicators: Dict[str, MarketCondition]) -> Dict[str, float]:
        scores = {}

        for regime in REGIME_TYPES.keys():
            if regime == "transition_market":
                # Special scoring for transition market
                score = self._score_transition_market(indicators)
                scores[regime] = score
                continue

            # Calculate base score
            base_score = 0.0
            weights_sum = 0.0

            for indicator, condition in indicators.items():
                regime_weights = REGIME_INDICATOR_WEIGHTS.get(regime, {})
                weight = regime_weights.get(indicator, 0.1)

                if weight == 0:
                    continue

                target_value = self._get_target_value(indicator, regime)
                if target_value is None:
                    continue

                # Calculate deviation from target
                deviation = abs(condition.value - target_value)

                # Score based on deviation (lower deviation = higher score)
                regime_score = max(0, 1 - min(deviation / 10, 1.0))

                # Adjust for trend and momentum
                if condition.trend == "increasing" and condition.momentum > 0:
                    regime_score = min(regime_score * 1.2, 1.0)
                elif condition.trend == "decreasing" and condition.momentum < 0:
                    regime_score = min(regime_score * 1.2, 1.0)

                base_score += regime_score * weight
                weights_sum += weight

            # Normalize score
            if weights_sum > 0:
                scores[regime] = base_score / weights_sum
            else:
                scores[regime] = 0.0

        return scores

    def _score_transition_market(self, indicators: Dict[str, MarketCondition]) -> float:
        # Transition market scoring is based on volatility and mixed signals
        volatility = 0.0
        mixed_signals = 0.0
        total_indicators = len(indicators)

        for indicator, condition in indicators.items():
            # High volatility indicators
            if "volatility" in indicator.lower() or "index" in indicator.lower():
                volatility += abs(condition.value) * condition.weight

            # Look for mixed signals (some increasing, some decreasing)
            if condition.trend != "stable":
                mixed_signals += 1

        # Higher score for high volatility and mixed signals
        transition_score = min((volatility + (mixed_signals / total_indicators * 0.5 if total_indicators > 0 else 0)), 1.0)

        return transition_score

    def _get_target_value(self, indicator: str, regime: str) -> Optional[float]:
        # Define target values for each indicator in each regime
        targets = {
            'bull_market': {
                'btc_price_change_24h': 5.0,
                'eth_price_change_24h': 5.0,
                'total_market_volume_24h': 1.0,
                'social_sentiment_score': 0.6,
                'fear_greed_index': 0.6,
            },
            'bear_market': {
                'btc_price_change_24h': -10.0,
                'eth_price_change_24h': -10.0,
                'liquidation_ratio': 2.0,
                'social_sentiment_score': -0.4,
                'funding_rate': -0.1,
            },
            'sideways_market': {
                'btc_price_change_24h': 0.0,
                'eth_price_change_24h': 0.0,
                'total_market_volume_24h': 0.0,
                'social_sentiment_score': 0.0,
                'fear_greed_index': 0.5,
            },
            'volatile_market': {
                'fear_greed_index': 0.8,
                'funding_rate': 0.1,
                'social_sentiment_score': 0.3,
            },
        }

        return targets.get(regime, {}).get(indicator)

    def _calculate_transition_probabilities(self, current_regime: str) -> Dict[str, float]:
        if not self.transitions:
            return {regime: 1.0 / len(REGIME_TYPES) for regime in REGIME_TYPES.keys()}

        # Calculate transition matrix from recent transitions
        transition_counts = {}
        for transition in self.transitions:
            key = f"{transition.from_regime}_{transition.to_regime}"
            transition_counts[key] = transition_counts.get(key, 0) + 1

        # Convert to probabilities
        total_transitions = sum(transition_counts.values())
        if total_transitions == 0:
            return {regime: 1.0 / len(REGIME_TYPES) for regime in REGIME_TYPES.keys()}

        probs = {}
        for regime in REGIME_TYPES.keys():
            from_key = f"{current_regime}_{regime}"
            probs[regime] = transition_counts.get(from_key, 0) / total_transitions

        # Normalize
        total_prob = sum(probs.values())
        if total_prob > 0:
            probs = {k: v / total_prob for k, v in probs.items()}
        else:
            probs = {regime: 1.0 / len(REGIME_TYPES) for regime in REGIME_TYPES.keys()}

        return probs

    def _calculate_regime_strength(self, regime: str, scores: Dict[str, float]) -> float:
        if not scores:
            return 0.0

        # Strength based on confidence and distance to other regimes
        my_score = scores.get(regime, 0)
        other_scores = [s for r, s in scores.items() if r != regime]

        if not other_scores:
            return my_score

        min_other_score = min(other_scores)
        strength = (my_score - min_other_score) / (1 - min_other_score + 1e-10)

        return min(strength, 1.0)

    def _get_key_factors(self, regime: str, indicators: Dict[str, MarketCondition]) -> List[str]:
        factors = []
        regime_config = REGIME_TYPES.get(regime, {})

        for indicator, condition in indicators.items():
            if abs(condition.value - self._get_target_value(indicator, regime) or 0) > 5:
                factors.append(f"{indicator}: {condition.value:.1f} (target: {self._get_target_value(indicator, regime) or 0:.1f})")

        if not factors:
            factors.append("Market appears stable with consistent indicators")

        return factors[:5]  # Return top 5 factors

    def _estimate_time_to_change(self, regime: str, scores: Dict[str, float]) -> float:
        if regime == "bull_market":
            # Bull markets tend to last longer
            return 14 + np.random.uniform(-7, 7)
        elif regime == "bear_market":
            # Bear markets change faster
            return 8 + np.random.uniform(-3, 5)
        elif regime == "sideways_market":
            # Sideways markets change medium speed
            return 10 + np.random.uniform(-5, 10)
        elif regime == "transition_market":
            # Transition markets resolve quickly
            return 3 + np.random.uniform(-2, 4)
        elif regime == "volatile_market":
            # Volatile markets change unpredictably
            return 5 + np.random.uniform(-5, 8)

        return 7

    def _record_regime_transition(self, from_regime: Optional[str], to_regime: str, timestamp: float, analysis: RegimeAnalysis):
        if from_regime is None:
            return

        transition = RegimeTransition(
            from_regime=from_regime,
            to_regime=to_regime,
            transition_time=timestamp,
            cause="Market condition analysis",
            contributing_factors=analysis.key_factors,
            impact_score=analysis.strength,
            recovery_time_estimate=analysis.time_to_change_estimate,
        )

        self.transitions.append(transition)

    def _get_cached_regime_analysis(self) -> RegimeAnalysis:
        if not self.regime_history:
            return self.analyze_regime(force_recalculate=True)

        latest_history = self.regime_history[-1]
        return RegimeAnalysis(
            regime=latest_history["regime"],
            confidence=latest_history.get("confidence", 0.5),
            probability=0.5,
            strength=0.5,
            indicators={},
            transition_probability={},
            recommended_strategy="hold",
            risk_assessment="medium",
            key_factors=["Cached data"],
            time_to_change_estimate=7,
            last_updated=latest_history.get("timestamp", time.time()),
        )

    def get_regime_summary(self) -> dict:
        if not self.current_regime:
            return {"status": "unknown", "message": "Market regime not yet detected"}

        analysis = self._get_cached_regime_analysis()

        return {
            "current_regime": self.current_regime,
            "confidence": analysis.confidence,
            "strength": analysis.strength,
            "recommended_strategy": analysis.recommended_strategy,
            "risk_level": REGIME_TYPES.get(self.current_regime, {}).get("risk_level", "medium"),
            "time_to_change_estimate": analysis.time_to_change_estimate,
            "last_updated": analysis.last_updated,
            "transitions_count": len(self.transitions),
        }

    def get_regime_transitions(self, limit: int = 10) -> List[dict]:
        return [
            {
                "from_regime": t.from_regime,
                "to_regime": t.to_regime,
                "transition_time": t.transition_time,
                "impact_score": t.impact_score,
                "recovery_time_estimate": t.recovery_time_estimate,
            }
            for t in reversed(self.transitions[-limit:])
        ]

    def save(self):
        self._save()

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Remove old indicator history
        for indicator in list(self.indicator_history.keys()):
            self.indicator_history[indicator] = [p for p in self.indicator_history[indicator] if p[0] >= cutoff]
            if not self.indicator_history[indicator]:
                del self.indicator_history[indicator]

        # Clean up old regime history
        self.regime_history = [h for h in self.regime_history if h["timestamp"] >= cutoff]

        if len(self.regime_history) > 100:
            self.regime_history = self.regime_history[-100:]

        logger.info(f"Cleaned up old market regime data: {len(self.regime_history)} regime entries")

    def get_summary_stats(self) -> dict:
        return {
            "current_regime": self.current_regime,
            "regime_history_count": len(self.regime_history),
            "transitions_count": len(self.transitions),
            "indicators_monitored": len(self.indicator_history),
            "last_analysis": self.regime_history[-1]["timestamp"] if self.regime_history else None,
            "regime_durations": self._get_regime_durations(),
        }

    def _get_regime_durations(self) -> dict:
        if not self.regime_history:
            return {}

        durations = {}
        for i in range(1, len(self.regime_history)):
            prev = self.regime_history[i-1]
            current = self.regime_history[i]
            duration = current["timestamp"] - prev["timestamp"]

            key = f"{prev['regime']} -> {current['regime']}"
            durations[key] = duration / 86400  # Convert to days

        return durations


simple_regime_detector = MarketRegimeDetector()
