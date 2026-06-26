import re

NAME_BLACKLIST = re.compile(
    r'(nut|god|inu|elon|trump|musk|pepe|wojak|chad|based|cum|rekt|rug|scam|honey|pump|dump|safe|moon|sats|whale|dragon|kira|test|fuck|shit|ass|butt|poop|fart|balls)',
    re.IGNORECASE
)

HIGH_VALUE_WORDS = re.compile(
    r'(cat|dog|frog|duck|bird|fish|crab|lobster|shrimp|tuna|whale|panda|baby|king|queen|lord|lady|star|gold|blue|red|green|dark|light|fire|ice|thunder|storm|wave|water|wind|sky|sun|moon|night|day|dream|magic|crypto|sol|ray|pump|coin|token)',
    re.IGNORECASE
)


def score_gmgn_token(token: dict) -> dict:
    """
    Data-driven scoring using learned winner patterns.
    Delegates to learned_scorer for actual similarity calculation.
    Falls back to simple heuristic if no winner data.
    """
    try:
        from learned_scorer import score_token
        result = score_token(token)
        # Map verdict to old format for compatibility
        verdict_map = {"STRONG": "CONFIRMED", "GOOD": "PROBABLE", "FAIR": "WEAK", "WEAK": "SKIP", "NO_DATA": "SKIP"}
        return {
            "score": result["score"],  # Keep 0-1 scale
            "verdict": verdict_map.get(result["verdict"], "SKIP"),
            "matched": result.get("matched", []),
            "holders": token.get("holders", 0),
            "dev_balance_sol": token.get("dev_balance_sol", 0),
            "liq_usd": token.get("liq_usd", 0),
            "signal_type": token.get("signal_type", ""),
            "top10_pct": token.get("top10_pct", 0),
            "dev_status": token.get("dev_status", "UNKNOWN"),
            "learned_score": result["score"],
            "best_similarity": result.get("best_similarity", 0),
        }
    except ImportError:
        # Fallback: simple non-penalizing score
        return _simple_score(token)


def _simple_score(token: dict) -> dict:
    """Simple fallback score when learned_scorer is unavailable."""
    score = 0.5  # Start neutral
    matched = []

    liq = token.get("liq_usd", 0)
    holders = token.get("holders", 0)
    sig = token.get("signal_type", "")

    # Positive signals (from data, not assumptions)
    if 10000 <= liq <= 60000:
        score += 0.1
        matched.append(f"Liq ${liq:,.0f} (sweet spot)")
    if 5 <= holders <= 15:
        score += 0.1
        matched.append(f"Holders {holders} (sweet spot)")
    if sig in ("KOTH", "FDV_SURGE", "CTO"):
        score += 0.1
        matched.append(f"Signal {sig}")

    score = max(0.0, min(1.0, score))
    verdict = "PROBABLE" if score >= 0.6 else "WEAK" if score >= 0.4 else "SKIP"
    return {
        "score": score * 2 - 1,
        "verdict": verdict,
        "matched": matched,
        "holders": holders,
        "liq_usd": liq,
        "signal_type": sig,
    }
    holders = token.get("holders", 0)
    dev_bal = token.get("dev_balance_sol", 0)
    liq = token.get("liq_usd", 0)
    name = (token.get("name", "") or token.get("symbol", ""))
    symbol = token.get("symbol", "")
    total = 0.0
    matched = []

    if holders <= 3:
        total += 0.30; matched.append("holders ≤3")
    elif holders <= 7:
        total += 0.20; matched.append("holders 4-7")
    elif holders <= 15:
        total += 0.10; matched.append("holders 8-15")
    elif holders <= 30:
        total += 0.05; matched.append("holders 15-30")

    if 0 < dev_bal <= 0.5:
        total += 0.30; matched.append("dev <0.5 SOL")
    elif 0.5 < dev_bal <= 1.0:
        total += 0.20; matched.append("dev 0.5-1 SOL")
    elif dev_bal > 5.0:
        total -= 0.15; matched.append("dev >5 SOL (HIGH)")

    if 1.0 <= dev_bal <= 2.0:
        total -= 0.30; matched.append("dev 1-2 SOL (RISKY)")

    if 5000 <= liq <= 10000:
        total += 0.15; matched.append("liq $5-10K")
    elif 10000 < liq <= 15000:
        total += 0.20; matched.append("liq $10-15K")
    elif 15000 < liq <= 20000:
        total += 0.10; matched.append("liq $15-20K")
    elif liq > 50000:
        total += 0.05; matched.append("liq >$50K")

    signal_type = token.get("signal_type", "UNKNOWN")
    sig_score = SIGNAL_TYPE_SCORES.get(signal_type, 0)
    if sig_score != 0:
        total += sig_score
        matched.append(f"signal:{signal_type} ({sig_score:+.2f})")

    top10 = token.get("top10_pct", 0)
    if 0 < top10 <= 15:
        total += 0.10; matched.append(f"top10 {top10:.1f}% (safe)")
    elif 15 < top10 <= 25:
        pass
    elif 25 < top10 <= 40:
        total -= 0.15; matched.append(f"top10 {top10:.1f}% (risky)")
    elif top10 > 40:
        total -= 0.30; matched.append(f"top10 {top10:.1f}% (DANGEROUS)")

    security_score = 0
    if token.get("no_mint"):
        security_score += 0.05; matched.append("NoMint")
    if token.get("blacklist_safe"):
        security_score += 0.05; matched.append("Blacklist safe")
    if token.get("burnt"):
        security_score += 0.05; matched.append("Burnt")
    total += security_score

    dev_status = token.get("dev_status", "UNKNOWN")
    dev_status_score = DEV_STATUS_SCORES.get(dev_status, 0)
    if dev_status_score != 0:
        total += dev_status_score
        matched.append(f"dev:{dev_status} ({dev_status_score:+.2f})")

    pc_5m = token.get("price_change_5m", 0)
    pc_1h = token.get("price_change_1h", 0)
    pc_6h = token.get("price_change_6h", 0)
    if pc_6h > 200:
        total += 0.15; matched.append(f"6h +{pc_6h:.0f}% (MOONING)")
    elif pc_6h > 100:
        total += 0.10; matched.append(f"6h +{pc_6h:.0f}%")
    elif pc_1h > 50:
        total += 0.10; matched.append(f"1h +{pc_1h:.0f}%")
    elif pc_5m > 30:
        total += 0.05; matched.append(f"5m +{pc_5m:.0f}%")

    vol = token.get("volume_5m", 0)
    txns = token.get("txns_5m", 0)
    if vol > 100000:
        total += 0.10; matched.append(f"vol5m ${vol/1000:.0f}K (HIGH)")
    elif vol > 30000:
        total += 0.05; matched.append(f"vol5m ${vol/1000:.0f}K")

    name_text = f"{name} {symbol}"
    if NAME_BLACKLIST.search(name_text):
        total -= 0.20; matched.append("blacklisted name")

    high_value_matches = HIGH_VALUE_WORDS.findall(name_text)
    if high_value_matches:
        bonus = min(len(high_value_matches) * 0.05, 0.15)
        total += bonus; matched.append(f"+{bonus:.2f} name pattern")

    total = max(-1.0, min(1.0, total))
    verdict = "CONFIRMED" if total >= 0.7 else "PROBABLE" if total >= 0.5 else "WEAK" if total >= 0.3 else "SKIP"

    return {
        "score": round(total, 2),
        "verdict": verdict,
        "matched": matched,
        "holders": holders,
        "dev_balance_sol": dev_bal,
        "liq_usd": liq,
        "signal_type": signal_type,
        "top10_pct": top10,
        "dev_status": dev_status,
    }


def check_similarity_to_patterns(token: dict, insights: dict = None) -> dict:
    """Check if a token matches known winning patterns from channel insights."""
    if not insights:
        try:
            import json, os
            bot_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")
            with open(bot_data) as f:
                data = json.load(f)
            insights = data.get("model", {}).get("channel_insights", data.get("model", {}).get("learned_patterns", {}))
        except Exception:
            return {"similarity": 0, "matched_patterns": [], "confidence": "NONE"}

    patterns = insights.get("patterns", insights)
    matched = []
    score = 0

    holders = token.get("holders", 0)
    if 1 <= holders <= 5:
        hp = patterns.get("holder_ranges", {}).get("1-5", {})
        if hp.get("win_rate", 0) > 40:
            matched.append(f"Holders 1-5 ({hp['win_rate']}% win)")
            score += 0.2
    elif 6 <= holders <= 10:
        hp = patterns.get("holder_ranges", {}).get("6-10", {})
        if hp.get("win_rate", 0) > 35:
            matched.append(f"Holders 6-10 ({hp['win_rate']}% win)")
            score += 0.15

    dev = token.get("dev_balance_sol", 0)
    if 0 < dev <= 0.5:
        dp = patterns.get("dev_ranges", {}).get("0-0.5", {})
        if dp.get("win_rate", 0) > 40:
            matched.append(f"Dev <0.5 SOL ({dp['win_rate']}% win)")
            score += 0.2
    elif 0.5 < dev <= 1:
        dp = patterns.get("dev_ranges", {}).get("0.5-1", {})
        if dp.get("win_rate", 0) > 35:
            matched.append(f"Dev 0.5-1 SOL ({dp['win_rate']}% win)")
            score += 0.15

    liq = token.get("liq_usd", 0)
    liq_key = None
    if 0 < liq <= 5000: liq_key = "0-5K"
    elif 5000 < liq <= 10000: liq_key = "5-10K"
    elif 10000 < liq <= 20000: liq_key = "10-20K"
    elif 20000 < liq <= 50000: liq_key = "20-50K"
    elif liq > 50000: liq_key = "50K+"
    if liq_key:
        lp = patterns.get("liq_ranges", {}).get(liq_key, {})
        if lp.get("win_rate", 0) > 35:
            matched.append(f"Liq {liq_key} ({lp['win_rate']}% win)")
            score += 0.15

    sig = token.get("signal_type", "UNKNOWN")
    sp = patterns.get("signal_types", {}).get(sig, {})
    if sp.get("win_rate", 0) > 40:
        matched.append(f"Signal {sig} ({sp['win_rate']}% win)")
        score += 0.25

    ch_name = token.get("source_channel_name", "")
    cp = patterns.get("channels", {}).get(ch_name, {})
    if cp.get("win_rate", 0) > 40:
        matched.append(f"Channel {ch_name} ({cp['win_rate']}% win)")
        score += 0.1

    score = min(1.0, score)
    confidence = "HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.4 else "LOW" if score >= 0.2 else "NONE"

    return {
        "similarity": round(score, 2),
        "matched_patterns": matched,
        "confidence": confidence,
    }


def generate_signal_alert(token: dict, score_result: dict) -> str:
    """Generate a Telegram alert message for a scored token."""
    sym = token.get("symbol", "?")
    name = token.get("name", "?")
    ca = token.get("ca", "?")
    mcp = token.get("mcp", 0)
    liq = token.get("liq_usd", 0)
    holders = token.get("holders", 0)
    sig = token.get("signal_type", "?")
    top10 = token.get("top10_pct", 0)
    pc_5m = token.get("price_change_5m", 0)
    pc_1h = token.get("price_change_1h", 0)
    vol = token.get("volume_5m", 0)
    txns = token.get("txns_5m", 0)
    score = score_result.get("score", 0)
    verdict = score_result.get("verdict", "SKIP")
    matched = score_result.get("matched", [])

    # Emoji for verdict
    verdict_emoji = {"CONFIRMED": "🟢", "PROBABLE": "🟡", "WEAK": "🟠", "SKIP": "🔴"}.get(verdict, "⚪")

    lines = [
        f"{verdict_emoji} <b>{verdict}: ${sym}</b> ({name})",
        f"📊 Score: <b>{score:.2f}</b>",
        f"📡 Signal: {sig}",
        f"💰 MCP: ${mcp:,.0f} | Liq: ${liq:,.0f}",
        f"👥 Holders: {holders} | Top10: {top10:.1f}%",
        f"📈 5m: {pc_5m:+.1f}% | 1h: {pc_1h:+.1f}%",
        f"🎲 Vol5m: ${vol:,.0f} | Txns: {txns}",
        f"🔗 <a href='https://gmgn.ai/sol/token/{ca}'>GMGN</a> | "
        f"<a href='https://dexscreener.com/solana/{ca}'>Dex</a> | "
        f"<a href='https://t.me/solanasniperbot?start={ca}'>Buy</a>",
    ]
    if matched:
        lines.append(f"✅ {' | '.join(matched[:4])}")
    return "\n".join(lines)


def should_alert(token: dict, score_result: dict) -> bool:
    """Determine if a token should trigger an alert."""
    score = score_result.get("score", 0)
    verdict = score_result.get("verdict", "SKIP")
    holders = token.get("holders", 0)
    mcp = token.get("mcp", 0)
    sig = token.get("signal_type", "")

    # Alert thresholds
    if verdict in ("CONFIRMED", "PROBABLE") and score >= 0.5:
        return True
    if score >= 0.6:
        return True
    if sig in ("KOTH", "FDV_SURGE") and holders >= 10 and mcp >= 5000:
        return True
    return False
