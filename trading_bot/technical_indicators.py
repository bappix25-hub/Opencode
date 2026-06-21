import numpy as np
import pandas as pd
import logging
import os
import json
import time
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from scipy import stats, signal
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger("technical_indicators")

TECHNICAL_INDICATORS_FILE = os.path.join(os.path.dirname(__file__), "data", "technical_indicators.json")

# Indicator configurations
INDICATOR_CONFIGS = {
    'sma': {'period': 20, 'weight': 0.2},
    'ema': {'period': 12, 'weight': 0.2},
    'rsi': {'period': 14, 'overbought': 70, 'oversold': 30, 'weight': 0.15},
    'macd': {'fast_period': 12, 'slow_period': 26, 'signal_period': 9, 'weight': 0.15},
    'bollinger_bands': {'period': 20, 'std_dev': 2, 'weight': 0.15},
    'atr': {'period': 14, 'weight': 0.1},
    'stochastics': {'period': 14, 'weight': 0.1},
    'williams_r': {'period': 14, 'weight': 0.1},
    'trend_score': {'window': 20, 'weight': 0.1},
    'volatility_score': {'period': 20, 'weight': 0.1},
    'momentum_score': {'period': 10, 'weight': 0.1},
}

# Pattern recognition scores
PATTERN_SCORES = {
    'head_and_shoulders': -0.8,
    'double_top': -0.7,
    'double_bottom': 0.7,
    'triple_top': -0.6,
    'triple_bottom': 0.6,
    'cup_handle': 0.5,
    'rounding_bottom': 0.6,
    'ascending_triangle': 0.4,
    'descending_triangle': -0.4,
    'symmetrical_triangle': 0.0,
    'flag_continuation': 0.3,
    'pennant_continuation': 0.3,
    'inverse_head_and_shoulders': 0.8,
}


@dataclass
class IndicatorValue:
    name: str
    value: float
    timestamp: float
    trend: str  # 'increasing', 'decreasing', 'stable'
    momentum: float
    strength: float
    signal: str  # 'buy', 'sell', 'neutral'
    confidence: float


@dataclass
class PatternAnalysis:
    pattern_name: str
    score: float
    confidence: float
    start_time: float
    end_time: float
    strength: float
    direction: str  # 'bullish', 'bearish', 'neutral'


@dataclass
class IndicatorThreshold:
    indicator: str
    buy_threshold: float
    sell_threshold: float
    neutral_threshold: float
    min_strength: float


class TechnicalIndicatorEngine:
    def __init__(self):
        self.indicator_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.pattern_history: Dict[str, List[PatternAnalysis]] = defaultdict(list)
        self.thresholds: Dict[str, IndicatorThreshold] = {}
        self.custom_indicators: Dict[str, callable] = {}
        self.indicator_cache: Dict[str, Dict[str, IndicatorValue]] = defaultdict(dict)

        self._load_config()
        self._initialize_thresholds()

    def _load_config(self):
        try:
            if os.path.exists(TECHNICAL_INDICATORS_FILE):
                with open(TECHNICAL_INDICATORS_FILE, "r") as f:
                    data = json.load(f)
                    self.indicator_history = {
                        indicator: deque([(ts, val) for ts, val in prices], maxlen=1000)
                        for indicator, prices in data.get("indicator_history", {}).items()
                    }

            logger.info(f"Technical indicators loaded: {len(self.indicator_history)} indicators")
        except Exception as e:
            logger.error(f"Error loading technical indicators: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(TECHNICAL_INDICATORS_FILE), exist_ok=True)
            data = {
                "indicator_history": {
                    indicator: [(ts, val) for ts, val in prices]
                    for indicator, prices in self.indicator_history.items()
                },
                "saved_at": time.time(),
            }
            with open(TECHNICAL_INDICATORS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving technical indicators: {e}")

    def _initialize_thresholds(self):
        self.thresholds = {
            'sma': IndicatorThreshold('sma', 0, 0, 0, 0.7),
            'ema': IndicatorThreshold('ema', 0, 0, 0, 0.7),
            'rsi': IndicatorThreshold('rsi', 30, 70, 50, 0.6),
            'macd': IndicatorThreshold('macd', -0.1, 0.1, 0, 0.5),
            'bollinger_bands': IndicatorThreshold('bollinger_bands', -0.2, 0.2, 0, 0.5),
            'atr': IndicatorThreshold('atr', 0.1, 0.3, 0.2, 0.4),
            'stochastics': IndicatorThreshold('stochastics', 20, 80, 50, 0.5),
            'williams_r': IndicatorThreshold('williams_r', -80, -20, -50, 0.5),
            'trend_score': IndicatorThreshold('trend_score', -0.5, 0.5, 0, 0.6),
            'volatility_score': IndicatorThreshold('volatility_score', 0.1, 0.5, 0.3, 0.4),
            'momentum_score': IndicatorThreshold('momentum_score', -0.3, 0.3, 0, 0.5),
        }

    def update_price_history(self, symbol: str, prices: List[float], timestamps: List[float] = None):
        if not prices:
            return

        if timestamps is None:
            timestamps = [time.time() - (len(prices) - i) * 60 for i in range(len(prices))]

        key = f"{symbol}_price"
        self.indicator_history[key] = deque(
            zip(timestamps, prices),
            maxlen=1000
        )

        # Calculate technical indicators
        self._calculate_all_indicators(symbol)

    def _calculate_all_indicators(self, symbol: str):
        key = f"{symbol}_price"
        if key not in self.indicator_history:
            return

        prices = [p for ts, p in self.indicator_history[key]]
        timestamps = [ts for ts, p in self.indicator_history[key]]

        if len(prices) < 20:
            return

        # Calculate all indicators
        indicators = {}

        indicators['sma'] = self._calculate_sma(prices, 20)
        indicators['ema'] = self._calculate_ema(prices, 12)
        indicators['rsi'] = self._calculate_rsi(prices, 14)
        indicators['macd'] = self._calculate_macd(prices, 12, 26, 9)
        indicators['bollinger_bands'] = self._calculate_bollinger_bands(prices, 20)
        indicators['atr'] = self._calculate_atr(prices, 14)
        indicators['stochastics'] = self._calculate_stochastics(prices, 14)
        indicators['williams_r'] = self._calculate_williams_r(prices, 14)
        indicators['trend_score'] = self._calculate_trend_score(prices, 20)
        indicators['volatility_score'] = self._calculate_volatility_score(prices, 20)
        indicators['momentum_score'] = self._calculate_momentum_score(prices, 10)

        # Store in cache
        current_time = timestamps[-1]
        for name, value in indicators.items():
            if value is not None:
                threshold = self.thresholds[name]
                trend, momentum = self._calculate_trend(prices[-10:], value)
                strength = self._calculate_strength(value, threshold, name)
                signal = self._generate_signal(value, threshold, name)
                confidence = self._calculate_confidence(value, name, trend, momentum)

                self.indicator_cache[symbol] = self.indicator_cache.get(symbol, {})
                self.indicator_cache[symbol][name] = IndicatorValue(
                    name=name,
                    value=value,
                    timestamp=current_time,
                    trend=trend,
                    momentum=momentum,
                    strength=strength,
                    signal=signal,
                    confidence=confidence,
                )

    def _calculate_sma(self, prices: List[float], period: int) -> float:
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        if len(prices) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        return ema

    def _calculate_rsi(self, prices: List[float], period: int) -> float:
        if len(prices) < period + 1:
            return None

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        if len(gains) < period:
            return None

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_macd(self, prices: List[float], fast_period: int, slow_period: int, signal_period: int) -> Dict[str, float]:
        if len(prices) < slow_period + signal_period:
            return None

        fast_ema = self._calculate_ema(prices, fast_period)
        slow_ema = self._calculate_ema(prices, slow_period)

        if fast_ema is None or slow_ema is None:
            return None

        macd = fast_ema - slow_ema
        signal_line = self._calculate_ema([macd], signal_period)

        return {
            'macd': macd,
            'signal': signal_line,
            'histogram': macd - signal_line
        }

    def _calculate_bollinger_bands(self, prices: List[float], period: int, std_dev: int) -> Dict[str, float]:
        if len(prices) < period:
            return None

        middle_band = self._calculate_sma(prices, period)
        if middle_band is None:
            return None

        std = np.std(prices[-period:])
        upper_band = middle_band + (std_dev * std)
        lower_band = middle_band - (std_dev * std)

        return {
            'upper': upper_band,
            'middle': middle_band,
            'lower': lower_band,
            'width': upper_band - lower_band,
            'position': 'above_middle' if prices[-1] > middle_band else 'below_middle'
        }

    def _calculate_atr(self, prices: List[float], period: int) -> float:
        if len(prices) < period + 1:
            return None

        tr_values = []
        for i in range(1, len(prices)):
            high = prices[i]
            low = prices[i]
            prev_close = prices[i-1]

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)

        if len(tr_values) < period:
            return None

        atr = sum(tr_values[-period:]) / period

        return atr

    def _calculate_stochastics(self, prices: List[float], period: int) -> Dict[str, float]:
        if len(prices) < period:
            return None

        highest_high = max(prices[-period:])
        lowest_low = min(prices[-period:])

        if highest_high == lowest_low:
            return {'k': 50, 'd': 50}

        k = (prices[-1] - lowest_low) / (highest_high - lowest_low) * 100

        # Calculate D line (3-period SMA of K)
        k_values = []
        for i in range(1, len(prices)):
            daily_high = prices[max(0, i-period+1):i+1]
            daily_low = prices[max(0, i-period+1):i+1]

            if daily_high and daily_low:
                day_k = (prices[i] - min(daily_low)) / (max(daily_high) - min(daily_low)) * 100
                k_values.append(day_k)

        if not k_values:
            return {'k': k, 'd': 50}

        d = sum(k_values[-3:]) / 3 if len(k_values) >= 3 else k_values[-1]

        return {'k': k, 'd': d}

    def _calculate_williams_r(self, prices: List[float], period: int) -> float:
        if len(prices) < period:
            return None

        highest_high = max(prices[-period:])
        lowest_low = min(prices[-period:])

        if highest_high == lowest_low:
            return -50

        williams_r = (highest_high - prices[-1]) / (highest_high - lowest_low) * -100

        return williams_r

    def _calculate_trend_score(self, prices: List[float], window: int) -> float:
        if len(prices) < window + 1:
            return None

        # Calculate linear regression slope
        x = np.arange(len(prices))
        y = np.array(prices)

        slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)

        # Normalize slope (higher = more bullish)
        # Use annual return rate (daily slope * 252)
        annual_return = slope * 252 * 100

        # Cap the score to reasonable range
        score = max(-10, min(10, annual_return / 10))

        return score

    def _calculate_volatility_score(self, prices: List[float], period: int) -> float:
        if len(prices) < period:
            return None

        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        volatility = np.std(returns[-period:]) * np.sqrt(252) * 100  # Annualized volatility

        # Normalize to 0-1 scale
        score = min(volatility / 50, 1.0)  # Cap at 50% annual volatility

        return score

    def _calculate_momentum_score(self, prices: List[float], period: int) -> float:
        if len(prices) < period + 1:
            return None

        current_price = prices[-1]
        past_price = prices[-period-1]

        momentum = (current_price - past_price) / past_price * 100

        # Normalize momentum
        score = max(-10, min(10, momentum / 10))

        return score

    def _calculate_trend(self, prices: List[float], current_value: float) -> Tuple[str, float]:
        if len(prices) < 5:
            return "stable", 0.0

        recent_prices = prices[-5:]
        changes = [(recent_prices[i] - recent_prices[i-1]) / recent_prices[i-1] * 100 for i in range(1, len(recent_prices))]

        avg_change = sum(changes) / len(changes)

        if avg_change > 0.5:
            trend = "increasing"
        elif avg_change < -0.5:
            trend = "decreasing"
        else:
            trend = "stable"

        # Calculate momentum (rate of change acceleration)
        if len(changes) >= 2:
            momentum_change = changes[-1] - changes[-2]
        else:
            momentum_change = 0.0

        return trend, momentum_change

    def _calculate_strength(self, value: float, threshold: IndicatorThreshold, indicator_name: str) -> float:
        if indicator_name == 'rsi':
            if value >= threshold.buy_threshold:
                return min((value - threshold.buy_threshold) / (100 - threshold.buy_threshold), 1.0)
            elif value <= threshold.sell_threshold:
                return min((threshold.sell_threshold - value) / threshold.sell_threshold, 1.0)
            else:
                return 0.0

        elif indicator_name == 'macd':
            macd_dict = value
            if macd_dict and 'histogram' in macd_dict:
                hist = macd_dict['histogram']
                if hist > 0:
                    return min(hist / 10, 1.0)
                elif hist < 0:
                    return min(abs(hist) / 10, 1.0)

        elif indicator_name == 'bollinger_bands':
            bb_dict = value
            if bb_dict and 'position' in bb_dict:
                if bb_dict['position'] == 'above_middle':
                    return min((bb_dict['upper'] - bb_dict['middle']) / (bb_dict['upper'] - bb_dict['lower']), 1.0)
                else:
                    return min((bb_dict['middle'] - bb_dict['lower']) / (bb_dict['upper'] - bb_dict['lower']), 1.0)

        else:
            if value >= threshold.buy_threshold:
                return min((value - threshold.buy_threshold) / (threshold.buy_threshold * 2), 1.0)
            elif value <= threshold.sell_threshold:
                return min((threshold.sell_threshold - value) / (threshold.sell_threshold * 2), 1.0)

        return 0.0

    def _generate_signal(self, value: float, threshold: IndicatorThreshold, indicator_name: str) -> str:
        if indicator_name == 'rsi':
            if value >= threshold.buy_threshold:
                return 'buy'
            elif value <= threshold.sell_threshold:
                return 'sell'
            else:
                return 'neutral'

        elif indicator_name == 'bollinger_bands':
            bb_dict = value
            if bb_dict and 'position' in bb_dict:
                if bb_dict['position'] == 'above_middle':
                    return 'buy'
                elif bb_dict['position'] == 'below_middle':
                    return 'sell'

        elif indicator_name in ['macd', 'stochastics', 'williams_r']:
            if indicator_name == 'macd':
                macd_dict = value
                if macd_dict and 'histogram' in macd_dict:
                    if macd_dict['histogram'] > 0:
                        return 'buy'
                    elif macd_dict['histogram'] < 0:
                        return 'sell'
            elif indicator_name == 'stochastics':
                stoch_dict = value
                if stoch_dict and 'k' in stoch_dict and 'd' in stoch_dict:
                    if stoch_dict['k'] > stoch_dict['d'] and stoch_dict['k'] < 80:
                        return 'buy'
                    elif stoch_dict['k'] < stoch_dict['d'] and stoch_dict['k'] > 20:
                        return 'sell'
            elif indicator_name == 'williams_r':
                if value < -80:
                    return 'buy'
                elif value > -20:
                    return 'sell'

        # Default logic for other indicators
        if value >= threshold.buy_threshold:
            return 'buy'
        elif value <= threshold.sell_threshold:
            return 'sell'
        else:
            return 'neutral'

    def _calculate_confidence(self, value: float, indicator_name: str, trend: str, momentum: float) -> float:
        base_confidence = 0.5

        # Adjust based on trend
        if trend == "increasing":
            base_confidence += 0.2
        elif trend == "decreasing":
            base_confidence -= 0.1

        # Adjust based on momentum
        abs_momentum = abs(momentum)
        if abs_momentum > 0.5:
            base_confidence += 0.2

        # Indicator-specific adjustments
        if indicator_name == 'rsi':
            # RSI confidence depends on how extreme the value is
            if value >= 70 or value <= 30:
                base_confidence += 0.2
        elif indicator_name == 'macd':
            # MACD confidence depends on histogram strength
            if indicator_name in self.indicator_cache:
                macd_dict = self.indicator_cache[indicator_name].get('macd')
                if macd_dict and 'histogram' in macd_dict:
                    hist_strength = abs(macd_dict['histogram']) / 10
                    base_confidence += hist_strength * 0.2

        return max(0.0, min(1.0, base_confidence))

    def detect_patterns(self, symbol: str) -> List[PatternAnalysis]:
        if symbol not in self.indicator_cache:
            return []

        cache = self.indicator_cache[symbol]
        patterns = []

        # Analyze for common patterns using multiple indicators
        if 'sma' in cache and 'ema' in cache and 'rsi' in cache:
            # Trend strength patterns
            sma = cache['sma'].value
            ema = cache['ema'].value
            rsi = cache['rsi'].value

            if sma > ema and rsi > 60:
                pattern = PatternAnalysis(
                    pattern_name='bullish_trend',
                    score=0.8,
                    confidence=cache['rsi'].confidence,
                    start_time=cache['sma'].timestamp - 100,
                    end_time=cache['sma'].timestamp,
                    strength=(sma - ema) / sma * 100,
                    direction='bullish',
                )
                patterns.append(pattern)

            elif sma < ema and rsi < 40:
                pattern = PatternAnalysis(
                    pattern_name='bearish_trend',
                    score=-0.8,
                    confidence=cache['rsi'].confidence,
                    start_time=cache['sma'].timestamp - 100,
                    end_time=cache['sma'].timestamp,
                    strength=(ema - sma) / ema * 100,
                    direction='bearish',
                )
                patterns.append(pattern)

        if 'bollinger_bands' in cache:
            bb = cache['bollinger_bands'].value
            if bb and 'position' in bb:
                if bb['position'] == 'above_middle':
                    pattern = PatternAnalysis(
                        pattern_name='bollinger_expansion',
                        score=0.6,
                        confidence=cache.get('rsi', {}).get('confidence', 0.5),
                        start_time=cache['bollinger_bands'].timestamp - 50,
                        end_time=cache['bollinger_bands'].timestamp,
                        strength=bb['width'] / 10,
                        direction='bullish',
                    )
                    patterns.append(pattern)

        if 'macd' in cache and 'rsi' in cache:
            macd = cache['macd'].value
            rsi = cache['rsi'].value

            if macd and 'histogram' in macd and rsi:
                if macd['histogram'] > 0.5 and rsi > 50:
                    pattern = PatternAnalysis(
                        pattern_name='macd_bullish_divergence',
                        score=0.7,
                        confidence=min(macd['histogram'], 1.0) * cache['rsi'].confidence,
                        start_time=cache['macd'].timestamp - 30,
                        end_time=cache['macd'].timestamp,
                        strength=min(macd['histogram'], 1.0),
                        direction='bullish',
                    )
                    patterns.append(pattern)

        return patterns

    def get_indicator_analysis(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.indicator_cache:
            return {
                'symbol': symbol,
                'indicators': {},
                'patterns': [],
                'overall_signal': 'neutral',
                'confidence': 0.0,
                'last_updated': time.time(),
            }

        cache = self.indicator_cache[symbol]

        # Combine signals from all indicators
        buy_signals = 0
        sell_signals = 0
        neutral_signals = 0
        total_confidence = 0.0
        signal_count = 0

        for name, indicator in cache.items():
            if indicator.signal == 'buy':
                buy_signals += 1
            elif indicator.signal == 'sell':
                sell_signals += 1
            else:
                neutral_signals += 1

            total_confidence += indicator.confidence
            signal_count += 1

        # Determine overall signal
        if buy_signals > sell_signals + neutral_signals:
            overall_signal = 'buy'
        elif sell_signals > buy_signals + neutral_signals:
            overall_signal = 'sell'
        else:
            overall_signal = 'neutral'

        # Calculate average confidence
        avg_confidence = total_confidence / signal_count if signal_count > 0 else 0.0

        return {
            'symbol': symbol,
            'indicators': {
                name: {
                    'value': indicator.value,
                    'signal': indicator.signal,
                    'strength': indicator.strength,
                    'confidence': indicator.confidence,
                    'trend': indicator.trend,
                    'momentum': indicator.momentum,
                }
                for name, indicator in cache.items()
            },
            'patterns': self.detect_patterns(symbol),
            'overall_signal': overall_signal,
            'confidence': avg_confidence,
            'last_updated': cache[list(cache.keys())[0]].timestamp if cache else time.time(),
        }

    def save(self):
        try:
            os.makedirs(os.path.dirname(TECHNICAL_INDICATORS_FILE), exist_ok=True)
            data = {
                "indicator_history": {
                    indicator: [(ts, val) for ts, val in prices]
                    for indicator, prices in self.indicator_history.items()
                },
                "saved_at": time.time(),
            }
            with open(TECHNICAL_INDICATORS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving technical indicators: {e}")

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Remove old indicator data
        for indicator in list(self.indicator_history.keys()):
            self.indicator_history[indicator] = [p for p in self.indicator_history[indicator] if p[0] >= cutoff]
            if not self.indicator_history[indicator]:
                del self.indicator_history[indicator]

        # Clear cache
        self.indicator_cache.clear()

        logger.info(f"Cleaned up old technical indicator data: {len(self.indicator_history)} indicators")

    def get_summary_stats(self) -> dict:
        return {
            "indicators_count": len(self.indicator_history),
            "cached_symbols": len(self.indicator_cache),
            "last_updated": max([ind.timestamp for ind in self.indicator_cache.values()], default=0),
            "avg_confidence": sum(ind.confidence for ind in self.indicator_cache.values()) / len(self.indicator_cache) if self.indicator_cache else 0,
            "signal_distribution": {
                'buy': sum(1 for ind in self.indicator_cache.values() if ind.signal == 'buy'),
                'sell': sum(1 for ind in self.indicator_cache.values() if ind.signal == 'sell'),
                'neutral': sum(1 for ind in self.indicator_cache.values() if ind.signal == 'neutral'),
            },
        }


technical_indicator_engine = TechnicalIndicatorEngine()
