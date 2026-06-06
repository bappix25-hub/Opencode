import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def get_env(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    if not value and not default:
        raise ValueError(f"Required environment variable {key} is not set")
    return value

def get_env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default

def get_env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def get_env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key, str(default)).lower()
    return value in ("true", "1", "yes", "on")

@dataclass
class Config:
    bot_token: str = get_env("BOT_TOKEN")
    chat_id: str = get_env("CHAT_ID")
    helius_api_key: str = get_env("HELIUS_API_KEY")
    
    pumpportal_ws: str = get_env("PUMPPORTAL_WS", "wss://pumpportal.fun/api/data")
    rugcheck_url: str = get_env("RUGCHECK_URL", "https://api.rugcheck.xyz/v1")
    data_file: str = get_env("DATA_FILE", "./bot_data.json")
    
    pump_multiplier: float = get_env_float("PUMP_MULTIPLIER", 3.0)
    ai_threshold: float = get_env_float("AI_THRESHOLD", 0.80)
    min_liquidity: float = get_env_float("MIN_LIQUIDITY", 2000)
    min_volume: float = get_env_float("MIN_VOLUME", 300)
    min_mcap: float = get_env_float("MIN_MCAP", 1000)
    max_mcap: float = get_env_float("MAX_MCAP", 2000000)
    
    scan_interval: int = get_env_int("SCAN_INTERVAL", 120)
    history_scan_interval: int = get_env_int("HISTORY_SCAN_INTERVAL", 3600)
    github_sync_interval: int = get_env_int("GITHUB_SYNC_INTERVAL", 21600)
    cleanup_interval: int = get_env_int("CLEANUP_INTERVAL", 3600)
    
    dex_max_retries: int = get_env_int("DEXSCREENER_MAX_RETRIES", 3)
    dex_base_delay: float = get_env_float("DEXSCREENER_BASE_DELAY", 1.0)
    
    enable_pre_migration: bool = get_env_bool("ENABLE_PRE_MIGRATION", True)
    enable_history_scan: bool = get_env_bool("ENABLE_HISTORY_SCAN", True)
    enable_github_sync: bool = get_env_bool("ENABLE_GITHUB_SYNC", True)

    paper_trading: bool = get_env_bool("PAPER_TRADING", True)
    paper_trade_sol: float = get_env_float("PAPER_TRADE_SOL", 0.1)
    paper_trade_sol_per_buy: float = get_env_float("PAPER_TRADE_SOL_PER_BUY", 0.01)
    paper_trade_max_positions: int = get_env_int("PAPER_TRADE_MAX_POSITIONS", 5)
    paper_trade_timeout_hours: int = get_env_int("PAPER_TRADE_TIMEOUT_HOURS", 3)

config = Config()