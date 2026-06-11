import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from typing import Optional

def setup_logging(name: str = "meme_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    log_file = os.environ.get("LOG_FILE", "").strip()
    if not log_file:
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log")

    try:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        daily = TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=7, encoding="utf-8"
        )
        daily.setFormatter(formatter)
        logger.addHandler(daily)

        size_cap = RotatingFileHandler(
            log_file + ".size", maxBytes=10 * 1024 * 1024,
            backupCount=3, encoding="utf-8"
        )
        size_cap.setFormatter(formatter)
        logger.addHandler(size_cap)
    except Exception as e:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
        logger.warning(f"⚠️ File logging disabled, using console: {e}")

    return logger

def format_number(n) -> str:
    try:
        n = float(n)
        if n >= 1_000_000:
            return f"${n/1_000_000:.2f}M"
        elif n >= 1_000:
            return f"${n/1_000:.1f}K"
        else:
            return f"${n:.2f}"
    except (ValueError, TypeError):
        return "$0"

def gmgn_link(address: str) -> str:
    return f"https://gmgn.ai/sol/token/{address}"

def get_launch_age(pair: dict) -> Optional[float]:
    try:
        created_at = pair.get("pairCreatedAt")
        if created_at:
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            return (now_ms - int(created_at)) / 1000
    except Exception:
        pass
    return None

def verify_pump(pair: dict, multiplier_threshold: float = 3.0) -> tuple[bool, float]:
    try:
        h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
        h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)
        best = max(h1, h6, h24)
        multiplier = 1 + best / 100
        return multiplier >= multiplier_threshold, round(multiplier, 2)
    except Exception:
        return False, 0.0