import logging
import sys
from datetime import datetime, timezone


def setup_logging(name: str = "trading_bot") -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    for lib in ("httpx", "httpcore", "telegram", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    has_file_handler = any(isinstance(h, logging.FileHandler) for h in root.handlers)

    if not has_file_handler:
        try:
            fh = logging.FileHandler("trading_bot.log", mode="a")
            fh.setFormatter(fmt)
            fh.setLevel(logging.DEBUG)
            root.addHandler(fh)
        except Exception:
            pass

    has_stream_handler = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers)

    if not has_stream_handler:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    return logger


def format_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.2f}K"
    elif value >= 1:
        return f"${value:.2f}"
    elif value >= 0.01:
        return f"${value:.4f}"
    else:
        return f"${value:.8f}"


def format_sol(value: float) -> str:
    return f"{value:.4f} SOL"


def format_pct(value: float) -> str:
    return f"{value:+.1f}%"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def age_str(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    elif seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    else:
        return f"{seconds / 86400:.1f}d"
