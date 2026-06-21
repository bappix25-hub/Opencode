import logging
from config import config

logger = logging.getLogger("risk")


class RiskManager:
    def __init__(self, learner=None):
        self.base_tp = config.tp_pct
        self.base_sl = config.sl_pct
        self.learner = learner

    def set_learner(self, learner):
        self.learner = learner

    def calculate_tp_sl(
        self, symbol: str, metrics: dict, pump_score: float
    ) -> tuple:
        if self.learner:
            return self.learner.get_recommended_tp_sl(symbol, metrics, pump_score)

        tp_pct = self.base_tp
        sl_pct = self.base_sl

        if pump_score >= 0.8:
            tp_pct *= 1.3
            sl_pct *= 0.9
        elif pump_score >= 0.6:
            tp_pct *= 1.1
            sl_pct *= 0.95

        liquidity = metrics.get("liquidity", 0)
        if liquidity > 50000:
            tp_pct *= 1.1
        elif liquidity < 10000:
            tp_pct *= 0.8
            sl_pct *= 0.8

        volume_5m = metrics.get("volume_5m", 0)
        if volume_5m > 100000:
            tp_pct *= 1.15

        return round(tp_pct, 1), round(sl_pct, 1)

    def calculate_dynamic_amount(
        self, sol_balance: float, pump_score: float, win_rate: float = 0.0
    ) -> float:
        base = config.sol_per_trade

        if pump_score >= 0.8:
            multiplier = 1.5
        elif pump_score >= 0.6:
            multiplier = 1.2
        else:
            multiplier = 1.0

        if win_rate > 60:
            multiplier *= 1.2
        elif win_rate < 40:
            multiplier *= 0.8

        amount = base * multiplier
        max_amount = sol_balance * 0.3
        amount = min(amount, max_amount)
        amount = max(amount, 0.001)

        return round(amount, 4)

    def should_trade(self, sol_balance: float, pump_score: float, win_rate: float = 0.0) -> tuple:
        if sol_balance < 0.005:
            return False, "Insufficient SOL balance"

        trade_amount = self.calculate_dynamic_amount(sol_balance, pump_score, win_rate)
        if sol_balance < trade_amount:
            return False, f"Need {trade_amount:.4f} SOL, have {sol_balance:.4f}"

        if pump_score < 0.3:
            return False, f"Low pump score: {pump_score:.2f}"

        if win_rate < 20 and self.learner and self.learner.model_stats["total_outcomes"] >= 10:
            return False, f"Win rate too low: {win_rate:.0f}%"

        return True, f"OK - trade {trade_amount:.4f} SOL"

    def get_position_size_for_type(self, coin_type: str, sol_balance: float) -> float:
        type_multipliers = {
            "micro_new": 0.6,
            "new_low_liq": 0.7,
            "new_mid_liq": 0.8,
            "blue_chip": 1.2,
            "high_volume": 1.0,
            "established": 1.1,
            "standard": 0.9,
        }
        mult = type_multipliers.get(coin_type, 0.9)
        base = config.sol_per_trade * mult
        return round(min(base, sol_balance * 0.3), 4)
