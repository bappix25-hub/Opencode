import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def get_env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")


@dataclass
class Config:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    solana_private_key: str = ""
    helius_api_key: str = ""
    birdeye_api_key: str = ""

    paper_trading: bool = True
    sol_per_trade: float = 0.01
    max_slippage_bps: int = 500
    tp_pct: float = 50.0
    sl_pct: float = -25.0

    volume_spike_multiplier: float = 5.0
    price_change_threshold: float = 15.0
    liquidity_change_threshold: float = 30.0
    scan_interval: int = 5

    api_rate_limit: float = 0.3
    api_max_retries: int = 3
    api_timeout: int = 10

    jupiter_quote_url: str = "https://api.jup.ag/swap/v2/quote"
    jupiter_swap_url: str = "https://api.jup.ag/swap/v2/swap"

    max_positions: int = 3
    min_mcap_for_trade: float = 3000.0
    max_mcap_for_trade: float = 1000000.0
    min_pump_score: float = 0.6
    max_sol_total: float = 0.05
    min_liquidity: float = 5000.0

    backtest_enabled: bool = True
    backtest_interval: int = 300
    price_snapshot_interval: int = 30
    pattern_analysis_interval: int = 3600
    weight_optimization_interval: int = 21600
    backtest_history_days: int = 7
    backtest_min_data_points: int = 10
    learning_rate: float = 0.1

    wallet_tracking_enabled: bool = True
    social_sentiment_enabled: bool = True
    chain_reaction_enabled: bool = True
    market_regime_enabled: bool = True
    technical_indicators_enabled: bool = True
    neural_engine_enabled: bool = True
    ensemble_enabled: bool = True
    advanced_trading_enabled: bool = True

    twitter_bearer_token: str = ""
    discord_webhooks: str = ""
    wallet_tracking_interval: int = 300
    social_sentiment_interval: int = 300
    chain_reaction_interval: int = 300
    market_regime_interval: int = 600
    technical_indicators_interval: int = 60
    neural_retrain_interval: int = 3600
    ensemble_retrain_interval: int = 3600

    trailing_stop_activation_pct: float = 10.0
    trailing_stop_callback_pct: float = 5.0
    partial_exit_enabled: bool = True
    dca_enabled: bool = True
    dca_num_entries: int = 3
    kelly_fraction: float = 0.25
    kelly_max_position_pct: float = 5.0

    def load(self):
        self.telegram_bot_token = get_env("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = get_env("TELEGRAM_CHAT_ID")
        self.solana_private_key = get_env("SOLANA_PRIVATE_KEY")
        self.helius_api_key = get_env("HELIUS_API_KEY")
        self.birdeye_api_key = get_env("BIRDEYE_API_KEY")

        self.paper_trading = get_env_bool("PAPER_TRADING", True)
        self.sol_per_trade = get_env_float("SOL_PER_TRADE", 0.01)
        self.max_slippage_bps = get_env_int("MAX_SLIPPAGE_BPS", 500)
        self.tp_pct = get_env_float("TP_PCT", 50.0)
        self.sl_pct = get_env_float("SL_PCT", -25.0)

        self.volume_spike_multiplier = get_env_float("VOLUME_SPIKE_MULTIPLIER", 5.0)
        self.price_change_threshold = get_env_float("PRICE_CHANGE_THRESHOLD", 15.0)
        self.liquidity_change_threshold = get_env_float("LIQUIDITY_CHANGE_THRESHOLD", 30.0)
        self.scan_interval = get_env_int("SCAN_INTERVAL", 10)

        self.api_rate_limit = get_env_float("API_RATE_LIMIT", 0.3)
        self.api_max_retries = get_env_int("API_MAX_RETRIES", 3)
        self.api_timeout = get_env_int("API_TIMEOUT", 10)

        self.jupiter_quote_url = get_env("JUPITER_QUOTE_URL", "https://api.jup.ag/swap/v2/quote")
        self.jupiter_swap_url = get_env("JUPITER_SWAP_URL", "https://api.jup.ag/swap/v2/swap")

        self.max_positions = get_env_int("MAX_POSITIONS", 3)
        self.min_mcap_for_trade = get_env_float("MIN_MCAP_FOR_TRADE", 3000.0)
        self.max_mcap_for_trade = get_env_float("MAX_MCAP_FOR_TRADE", 1000000.0)
        self.min_pump_score = get_env_float("MIN_PUMP_SCORE", 0.6)
        self.max_sol_total = get_env_float("MAX_SOL_TOTAL", 0.05)
        self.min_liquidity = get_env_float("MIN_LIQUIDITY", 5000.0)

        self.backtest_enabled = get_env_bool("BACKTEST_ENABLED", True)
        self.backtest_interval = get_env_int("BACKTEST_INTERVAL", 300)
        self.price_snapshot_interval = get_env_int("PRICE_SNAPSHOT_INTERVAL", 30)
        self.pattern_analysis_interval = get_env_int("PATTERN_ANALYSIS_INTERVAL", 3600)
        self.weight_optimization_interval = get_env_int("WEIGHT_OPTIMIZATION_INTERVAL", 21600)
        self.backtest_history_days = get_env_int("BACKTEST_HISTORY_DAYS", 7)
        self.backtest_min_data_points = get_env_int("BACKTEST_MIN_DATA_POINTS", 10)
        self.learning_rate = get_env_float("LEARNING_RATE", 0.1)

        self.wallet_tracking_enabled = get_env_bool("WALLET_TRACKING_ENABLED", True)
        self.social_sentiment_enabled = get_env_bool("SOCIAL_SENTIMENT_ENABLED", True)
        self.chain_reaction_enabled = get_env_bool("CHAIN_REACTION_ENABLED", True)
        self.market_regime_enabled = get_env_bool("MARKET_REGIME_ENABLED", True)
        self.technical_indicators_enabled = get_env_bool("TECHNICAL_INDICATORS_ENABLED", True)
        self.neural_engine_enabled = get_env_bool("NEURAL_ENGINE_ENABLED", True)
        self.ensemble_enabled = get_env_bool("ENSEMBLE_ENABLED", True)
        self.advanced_trading_enabled = get_env_bool("ADVANCED_TRADING_ENABLED", True)

        self.twitter_bearer_token = get_env("TWITTER_BEARER_TOKEN", "")
        self.discord_webhooks = get_env("DISCORD_WEBHOOKS", "")
        self.wallet_tracking_interval = get_env_int("WALLET_TRACKING_INTERVAL", 300)
        self.social_sentiment_interval = get_env_int("SOCIAL_SENTIMENT_INTERVAL", 300)
        self.chain_reaction_interval = get_env_int("CHAIN_REACTION_INTERVAL", 300)
        self.market_regime_interval = get_env_int("MARKET_REGIME_INTERVAL", 600)
        self.technical_indicators_interval = get_env_int("TECHNICAL_INDICATORS_INTERVAL", 60)
        self.neural_retrain_interval = get_env_int("NEURAL_RETRAIN_INTERVAL", 3600)
        self.ensemble_retrain_interval = get_env_int("ENSEMBLE_RETRAIN_INTERVAL", 3600)

        self.trailing_stop_activation_pct = get_env_float("TRAILING_STOP_ACTIVATION_PCT", 10.0)
        self.trailing_stop_callback_pct = get_env_float("TRAILING_STOP_CALLBACK_PCT", 5.0)
        self.partial_exit_enabled = get_env_bool("PARTIAL_EXIT_ENABLED", True)
        self.dca_enabled = get_env_bool("DCA_ENABLED", True)
        self.dca_num_entries = get_env_int("DCA_NUM_ENTRIES", 3)
        self.kelly_fraction = get_env_float("KELLY_FRACTION", 0.25)
        self.kelly_max_position_pct = get_env_float("KELLY_MAX_POSITION_PCT", 5.0)

        return self


config = Config().load()
