"""
social_tracker.py — Track social activity over time for tokens.

Key insight: Social links change over time.
- At launch: often no socials (new token)
- Days 1-3: socials added as community grows
- Winners: accumulate twitter, website, telegram over time
- Losers: socials stay empty or get removed

Tracks:
1. Social snapshot at each check (has_website, has_twitter, has_telegram)
2. Social growth rate (how fast socials added)
3. Winner vs loser social patterns
"""

import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("meme_bot.social_tracker")

SOCIAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_history.json")


def _load_social():
    try:
        if os.path.exists(SOCIAL_FILE):
            with open(SOCIAL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"tokens": {}, "patterns": {"winner": {}, "loser": {}}}


def _save_social(data):
    try:
        with open(SOCIAL_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save social data: {e}")


def record_social_snapshot(ca: str, token_data: dict):
    """
    Record social data snapshot for a token.
    Call this whenever we check a token's social data.
    """
    data = _load_social()
    tokens = data.setdefault("tokens", {})
    
    now = datetime.now(timezone.utc).timestamp()
    has_website = token_data.get("has_website", False)
    has_twitter = token_data.get("has_twitter", False)
    has_telegram = token_data.get("has_telegram", False)
    
    entry = tokens.setdefault(ca, {
        "symbol": token_data.get("symbol", "?"),
        "snapshots": [],
        "first_seen": now,
    })
    
    # Add snapshot
    snap = {
        "time": now,
        "has_website": has_website,
        "has_twitter": has_twitter,
        "has_telegram": has_telegram,
        "social_count": sum([has_website, has_twitter, has_telegram]),
    }
    
    # Only add if changed from last snapshot
    snaps = entry["snapshots"]
    if snaps:
        last = snaps[-1]
        if (last.get("has_website") == has_website and
            last.get("has_twitter") == has_twitter and
            last.get("has_telegram") == has_telegram):
            return  # No change, skip
    
    snaps.append(snap)
    entry["symbol"] = token_data.get("symbol", entry.get("symbol", "?"))
    
    # Keep only last 50 snapshots
    if len(snaps) > 50:
        entry["snapshots"] = snaps[-50:]
    
    _save_social(data)
    return snap


def record_token_outcome(ca: str, is_winner: bool):
    """Record winner/loser outcome for social pattern learning."""
    data = _load_social()
    tokens = data.get("tokens", {})
    
    entry = tokens.get(ca)
    if not entry:
        return
    
    snaps = entry.get("snapshots", [])
    if not snaps:
        return
    
    # Extract social features
    first_snap = snaps[0]
    last_snap = snaps[-1]
    total_snaps = len(snaps)
    
    # Growth: did socials increase over time?
    first_count = first_snap.get("social_count", 0)
    last_count = last_snap.get("social_count", 0)
    social_growth = last_count - first_count
    
    features = {
        "had_website_at_start": first_snap.get("has_website", False),
        "had_twitter_at_start": first_snap.get("has_twitter", False),
        "had_telegram_at_start": first_snap.get("has_telegram", False),
        "has_website_now": last_snap.get("has_website", False),
        "has_twitter_now": last_snap.get("has_twitter", False),
        "has_telegram_now": last_snap.get("has_telegram", False),
        "social_growth": social_growth,
        "total_checks": total_snaps,
        "socials_at_start": first_count,
        "socials_now": last_count,
    }
    
    # Store in winner or loser patterns
    category = "winner" if is_winner else "loser"
    patterns = data.setdefault("patterns", {})
    cat_patterns = patterns.setdefault(category, {})
    cat_patterns[ca] = features
    
    logger.info(
        f"[SOCIAL PATTERN] {entry.get('symbol', '?')} → {category}: "
        f"start={first_count} socials → now={last_count} socials "
        f"(growth={social_growth:+d})"
    )
    
    _save_social(data)


def get_social_patterns() -> dict:
    """
    Learn social patterns from winners vs losers.
    Returns comparison of winner vs loser social features.
    """
    data = _load_social()
    patterns = data.get("patterns", {})
    winners = patterns.get("winner", {})
    losers = patterns.get("loser", {})
    
    if not winners or not losers:
        return {"available": False, "reason": "insufficient data"}
    
    def avg_field(items, field):
        vals = [v.get(field, 0) for v in items.values() if field in v]
        return sum(vals) / len(vals) if vals else 0
    
    def pct_true(items, field):
        vals = [v.get(field, False) for v in items.values() if field in v]
        return sum(1 for v in vals if v) / len(vals) if vals else 0
    
    result = {
        "available": True,
        "winner_count": len(winners),
        "loser_count": len(losers),
        "winners": {
            "pct_had_website_start": pct_true(winners, "had_website_at_start"),
            "pct_had_twitter_start": pct_true(winners, "had_twitter_at_start"),
            "pct_had_telegram_start": pct_true(winners, "had_telegram_at_start"),
            "pct_has_website_now": pct_true(winners, "has_website_now"),
            "pct_has_twitter_now": pct_true(winners, "has_twitter_now"),
            "pct_has_telegram_now": pct_true(winners, "has_telegram_now"),
            "avg_social_growth": avg_field(winners, "social_growth"),
            "avg_socials_start": avg_field(winners, "socials_at_start"),
            "avg_socials_now": avg_field(winners, "socials_now"),
        },
        "losers": {
            "pct_had_website_start": pct_true(losers, "had_website_at_start"),
            "pct_had_twitter_start": pct_true(losers, "had_twitter_at_start"),
            "pct_had_telegram_start": pct_true(losers, "had_telegram_at_start"),
            "pct_has_website_now": pct_true(losers, "has_website_now"),
            "pct_has_twitter_now": pct_true(losers, "has_twitter_now"),
            "pct_has_telegram_now": pct_true(losers, "has_telegram_now"),
            "avg_social_growth": avg_field(losers, "social_growth"),
            "avg_socials_start": avg_field(losers, "socials_at_start"),
            "avg_socials_now": avg_field(losers, "socials_now"),
        },
    }
    
    # Compute social quality score difference
    w = result["winners"]
    l = result["losers"]
    result["insight"] = {
        "website_matters": w["pct_has_website_now"] > l["pct_has_website_now"],
        "twitter_matters": w["pct_has_twitter_now"] > l["pct_has_twitter_now"],
        "telegram_matters": w["pct_has_telegram_now"] > l["pct_has_telegram_now"],
        "social_growth_matters": w["avg_social_growth"] > l["avg_social_growth"],
        "winners_add_socials": w["avg_socials_now"] > w["avg_socials_start"],
        "losers_add_socials": l["avg_socials_now"] > l["avg_socials_start"],
    }
    
    return result


def get_social_score_from_history(ca: str) -> float:
    """
    Score a token based on its social growth history.
    Tokens that ADD socials over time are more likely winners.
    Returns 0-1 score.
    """
    data = _load_social()
    entry = data.get("tokens", {}).get(ca)
    
    if not entry or not entry.get("snapshots"):
        return 0.5  # No data, neutral
    
    snaps = entry["snapshots"]
    first = snaps[0]
    last = snaps[-1]
    
    score = 0.0
    
    # Social count (more = better)
    social_count = last.get("social_count", 0)
    score += social_count * 0.2  # 0-0.6
    
    # Social growth (growing = better)
    growth = last.get("social_count", 0) - first.get("social_count", 0)
    if growth > 0:
        score += 0.2  # Growing socials
    elif growth == 0 and social_count >= 2:
        score += 0.1  # Stable with 2+ socials
    
    # Twitter specifically matters for marketing
    if last.get("has_twitter"):
        score += 0.1
    
    # Website matters for legitimacy
    if last.get("has_website"):
        score += 0.1
    
    return min(1.0, score)


def backfill_from_tracked():
    """Backfill social data from existing tracked tokens."""
    try:
        tt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
        with open(tt_file) as f:
            tracked = json.load(f)
    except Exception:
        return 0
    
    data = _load_social()
    tokens = data.setdefault("tokens", {})
    count = 0
    
    for ca, t in tracked.items():
        if ca in tokens:
            continue
        
        # Create initial snapshot from available data
        has_website = t.get("has_website", False)
        has_twitter = t.get("has_twitter", False)
        has_telegram = t.get("has_telegram", False)
        
        # If no social data available, use defaults
        entry = {
            "symbol": t.get("symbol", "?"),
            "snapshots": [{
                "time": t.get("first_seen_ts", 0),
                "has_website": has_website,
                "has_twitter": has_twitter,
                "has_telegram": has_telegram,
                "social_count": sum([has_website, has_twitter, has_telegram]),
            }],
            "first_seen": t.get("first_seen_ts", 0),
        }
        tokens[ca] = entry
        count += 1
    
    _save_social(data)
    logger.info(f"[SOCIAL] Backfilled {count} tokens from tracked data")
    return count
