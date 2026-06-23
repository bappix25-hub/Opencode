import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from collections import defaultdict

try:
    from learner import extract_launch_features, record_launch
except ImportError:
    extract_launch_features = None
    record_launch = None

try:
    from maestro_client import get_client as _get_tg_client
except ImportError:
    _get_tg_client = None

logger = logging.getLogger("meme_bot.telegram_collector")

CHANNELS = [
    -1002122751413,  # Solana New Pool Alert
    -1002126036544,  # Solana LP Chat
    -1002037135333,  # Solana New Token Bot
    -1002064472392,  # Solana Listing Bot
    -1002202241417,  # GMGN Featured Signals(Lv2) - SOL
]

CHANNEL_NAMES = {
    -1002122751413: "New Pool Alert",
    -1002126036544: "LP Chat",
    -1002037135333: "New Token Bot",
    -1002064472392: "Listing Bot",
    -1002202241417: "GMGN Signals",
}

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_tracked_tokens.json")
BOT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")

_last_msg_ids: dict = {}
_tracked_tokens: dict = {}
_stats = {"scanned": 0, "new_tokens": 0, "errors": 0}
_ath_check_cache: dict = {}

GMGN_RE = re.compile(
    r'\*\*(?P<symbol>[^*]+)\*\*\s*\((?:\*\*)?(?P<name>[^(]+?)(?:\*\*)?\)\s*'
    r'.*?🎲 CA: `(?P<ca>[A-Za-z0-9]+)`'
    r'.*?💲 Price: \*\*(?P<price>[^*]+)\*\*'
    r'.*?🎯 Dex: \*\*(?P<dex>[^*]+)\*\*'
    r'.*?💡 MCP: \*\*(?P<mcp>[^*]+)\*\*'
    r'.*?💧 Liq池子: \*\*\$(?P<liq_usd>[0-9,.]+)\*\*.*?\((?:\*\*)?(?P<liq_sol>[0-9,.]+) SOL'
    r'.*?💰 Initial LP底池: \*\*(?P<initial_lp>[^%]+)%\*\*'
    r'.*?👥 Holder持有人: \*\*(?P<holders>\d+)\*\*'
    r'.*?👤 Renounced已弃权: (?P<renounced>[✅❌]+)'
    r'.*?Balance SOL: (?P<dev_balance_sol>[0-9,.]+)',
    re.DOTALL
)

CA_RE = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')

def _parse_gmgn_lv2_ca(text: str, symbol: str) -> str:
    """Extract CA from GMGN Lv2 message — CA follows the symbol+name block.
    Handles split CAs (CA split across lines with space)."""
    lines = text.split('\n')
    
    # First: look for CA on same line as symbol
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if symbol and f"${symbol}" in line:
            rest = line.split(symbol, 1)[1] if symbol in line else ""
            if '(' in rest:
                rest = rest.split(')', 1)[1] if ')' in rest else ""
            rest = rest.strip()
            m = CA_RE.match(rest)
            if m:
                return m.group(0)
    
    # Second: look for standalone CA on any line (including backtick-wrapped)
    for line in lines:
        line = line.strip()
        # Strip backticks if present
        if line.startswith('`') and line.endswith('`'):
            line = line[1:-1]
        m = CA_RE.match(line)
        if m and len(m.group(0)) >= 32:
            return m.group(0)
    
    # Third: look for CA after "🎲 CA:" marker
    for i, line in enumerate(lines):
        if '🎲' in line and 'CA' in line:
            # CA might be on same line after CA: or on next line
            rest = line.split('CA', 1)[1].strip().lstrip(':').strip()
            if rest.startswith('`') and rest.endswith('`'):
                rest = rest[1:-1]
            m = CA_RE.match(rest)
            if m and len(m.group(0)) >= 32:
                return m.group(0)
            # Check next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith('`') and next_line.endswith('`'):
                    next_line = next_line[1:-1]
                m = CA_RE.match(next_line)
                if m and len(m.group(0)) >= 32:
                    return m.group(0)
    
    # Third: handle split CA (e.g., "3HHTWR8mSdWGmNfH1gSpsFWvysWJ2DUwpmTs nTz1pump")
    # Look for lines after symbol line that contain partial CA
    found_symbol_line = False
    ca_parts = []
    for line in lines:
        line = line.strip()
        if symbol and f"${symbol}" in line:
            found_symbol_line = True
            continue
        if found_symbol_line and line:
            # Check if line looks like a CA (base58 chars, possibly with space)
            cleaned = line.replace(' ', '')
            if re.match(r'^[1-9A-HJ-NP-Za-km-z]{30,}$', cleaned):
                return cleaned
            # Or if line is a partial CA followed by more text
            m = CA_RE.match(line)
            if m and len(m.group(0)) >= 20:
                ca_parts.append(m.group(0))
                # Check next line for continuation
                continue
    
    # Fourth: try joining consecutive base58 lines after symbol
    found_symbol = False
    candidate = ""
    for line in lines:
        line = line.strip()
        if not line:
            if candidate:
                break
            continue
        if symbol and f"${symbol}" in line:
            found_symbol = True
            continue
        if found_symbol:
            cleaned = line.replace(' ', '')
            if re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', cleaned) and len(cleaned) >= 20:
                candidate += cleaned
                if len(candidate) >= 32:
                    return candidate
            elif candidate:
                break
    
    return ""

def parse_gmgn_lv2_msg(text: str):
    """Parse GMGN Featured Signals (Lv2) message format."""
    signal_type = "FEATURED_NEW"
    if "KOTH" in text or "King of the hill" in text:
        signal_type = "KOTH"
    elif "FDV Surge" in text or "市值飙升" in text:
        signal_type = "FDV_SURGE"
    elif "KOL" in text and ("FOMO" in text or "Buy" in text):
        signal_type = "KOL_FOMO"
    elif "Heavy Bought" in text:
        signal_type = "HEAVY_BOUGHT"
    elif "CTO" in text:
        signal_type = "CTO"
    elif "DEX Screener" in text or "DEXScreener" in text:
        signal_type = "DEXSOCIAL"
    elif "DEV Bought" in text or "PUMP DEV Bought" in text:
        signal_type = "DEV_BOUGHT"
    elif "DEV Sold" in text or "PUMP DEV" in text:
        signal_type = "DEV_SOLD"
    elif "Pump Completed" in text or "PUMP已满" in text:
        signal_type = "PUMP_COMPLETED"

    symbol = ""
    name = ""

    kol_match = re.search(r'Buy\s+([A-Za-z0-9_-]+)', text)
    if kol_match and "KOL" in text:
        symbol = kol_match.group(1).strip()

    if not symbol:
        heavy_match = re.search(r'Heavy Bought.*?\$([A-Za-z0-9_-]+)', text)
        if heavy_match:
            symbol = heavy_match.group(1).strip()

    if not symbol:
        sym_match = re.search(r'\*\*\$([^\s*]+)', text)
        if sym_match:
            candidate = sym_match.group(1).strip()
            if not candidate.isdigit() and len(candidate) < 20:
                symbol = candidate
                rest = text[sym_match.end():sym_match.end()+50]
                name_m = re.search(r'\*{0,2}\s*\*{0,2}\s*\(([^)]+)\)', rest)
                if name_m:
                    name = name_m.group(1).strip()
                else:
                    name = symbol

    if not symbol:
        sym_match3 = re.search(r'\*\*([^\s*]+)\s+\(([^)]+)\)\*\*', text)
        if sym_match3:
            candidate = sym_match3.group(1).strip()
            if not candidate.isdigit() and len(candidate) < 15 and candidate not in ('DEV', 'TOP', 'PUMP'):
                symbol = candidate
                name = sym_match3.group(2).strip()

    if not symbol:
        return None

    ca = _parse_gmgn_lv2_ca(text, symbol)
    if not ca or len(ca) < 32:
        return None

    mcp = 0
    m = re.search(r'(?:MCP|MCap|Market Cap)[:\s]*(?:\*\*)?\$?([0-9,.]+[KMB]?)(?:\*\*)?', text, re.IGNORECASE)
    if m:
        val_str = m.group(1).replace(",", "")
        if val_str.endswith("B"):
            mcp = float(val_str[:-1]) * 1e9
        elif val_str.endswith("M"):
            mcp = float(val_str[:-1]) * 1e6
        elif val_str.endswith("K"):
            mcp = float(val_str[:-1]) * 1e3
        else:
            mcp = float(val_str) if val_str else 0

    liq_usd = 0
    liq_sol = 0
    liq_burn_pct = 0
    liq_match = re.search(r'Liq[:\s]*(?:\*\*)?([0-9,.]+)(?:\*\*)?\s*(?:\*\*)?SOL(?:\*\*)?.*?\$([0-9,.]+[KMB]?)(?:\*\*)?', text, re.IGNORECASE)
    if liq_match:
        try:
            liq_sol = float(liq_match.group(1).replace(",", ""))
        except ValueError:
            pass
        liq_usd_str = liq_match.group(2).replace(",", "")
        try:
            if liq_usd_str.endswith("K"):
                liq_usd = float(liq_usd_str[:-1]) * 1e3
            elif liq_usd_str.endswith("M"):
                liq_usd = float(liq_usd_str[:-1]) * 1e6
            else:
                liq_usd = float(liq_usd_str)
        except ValueError:
            pass
    burn_match = re.search(r'🔥(\d+)%', text)
    if burn_match:
        liq_burn_pct = int(burn_match.group(1))

    holders = 0
    h_match = re.search(r'Holder[s]?[^\d]*(\d+)', text, re.IGNORECASE)
    if h_match:
        holders = int(h_match.group(1))

    top10_pct = 0
    t10_match = re.search(r'TOP\s*10[:\s]*(?:\*\*)?([0-9.]+)%?(?:\*\*)?', text)
    if t10_match:
        top10_pct = float(t10_match.group(1))

    no_mint = bool(re.search(r'NoMint|✅\s*NoMint', text))
    blacklist_safe = bool(re.search(r'✅\s*Blacklist', text))
    burnt = bool(re.search(r'✅\s*Burnt|🔥Burnt', text))

    dev_status = "UNKNOWN"
    dev_match = re.search(r'DEV[:\s]*(.*?)(?:\n|$)', text)
    if dev_match:
        dev_text = dev_match.group(1).strip()
        if "Sell All" in dev_text or "🚨" in dev_text:
            dev_status = "SELL_ALL"
        elif "Add Liquidity" in dev_text:
            dev_status = "ADD_LIQUIDITY"
        elif "Buy More" in dev_text or "Buy" in dev_text:
            dev_status = "BUY_MORE"
        elif "HOLD" in dev_text or "HOLDing" in dev_text:
            dev_status = "HOLD"
        elif "Burnt" in dev_text or "🔥" in dev_text:
            dev_status = "BURNT"

    price_change_5m = 0
    price_change_1h = 0
    price_change_6h = 0
    pc_match = re.search(r'5m\s*\|\s*1h\s*\|\s*6h[:\s]*(?:\*\*)?([>]?[-\d.]+)%?(?:\*\*)?\s*\|\s*(?:\*\*)?([>]?[-\d.]+)%?(?:\*\*)?\s*\|\s*(?:\*\*)?([>]?[-\d.]+)%?(?:\*\*)?', text)
    if pc_match:
        try:
            price_change_5m = float(pc_match.group(1).replace(">", ""))
            price_change_1h = float(pc_match.group(2).replace(">", ""))
            price_change_6h = float(pc_match.group(3).replace(">", ""))
        except ValueError:
            pass

    txns_5m = 0
    volume_5m = 0
    vol_match = re.search(r'5m\s*TXs/Vol[:\s]*(?:\*\*)?(\d+)(?:\*\*)?/(?:\*\*)?\$([0-9,.]+[KMB]?)(?:\*\*)?', text, re.IGNORECASE)
    if vol_match:
        try:
            txns_5m = int(vol_match.group(1))
            vol_str = vol_match.group(2).replace(",", "")
            if vol_str.endswith("K"):
                volume_5m = float(vol_str[:-1]) * 1e3
            elif vol_str.endswith("M"):
                volume_5m = float(vol_str[:-1]) * 1e6
            else:
                volume_5m = float(vol_str)
        except ValueError:
            pass

    return {
        "symbol": symbol,
        "name": name,
        "ca": ca,
        "signal_type": signal_type,
        "mcp": mcp,
        "liq_usd": liq_usd,
        "liq_sol": liq_sol,
        "liq_burn_pct": liq_burn_pct,
        "holders": holders,
        "top10_pct": top10_pct,
        "no_mint": no_mint,
        "blacklist_safe": blacklist_safe,
        "burnt": burnt,
        "dev_status": dev_status,
        "price_change_5m": price_change_5m,
        "price_change_1h": price_change_1h,
        "price_change_6h": price_change_6h,
        "txns_5m": txns_5m,
        "volume_5m": volume_5m,
        "first_seen": datetime.now(timezone.utc).timestamp(),
        "launch_mcp": mcp,
        "launch_liq": liq_usd,
        "ath_mcp": mcp,
        "ath_multiplier": 1.0,
        "status": "tracking",
        "last_check": datetime.now(timezone.utc).timestamp(),
    }

def parse_gmgn_msg(text: str):
    m = GMGN_RE.search(text)
    if not m:
        return None
    try:
        mcp_str = m.group("mcp").replace(",", "").strip()
        mcp = float(mcp_str) if mcp_str else 0
    except ValueError:
        mcp = 0
    try:
        liq_usd_str = m.group("liq_usd").replace(",", "")
        liq_usd = float(liq_usd_str) if liq_usd_str else 0
    except ValueError:
        liq_usd = 0
    try:
        liq_sol_str = m.group("liq_sol").replace(",", "")
        liq_sol = float(liq_sol_str) if liq_sol_str else 0
    except ValueError:
        liq_sol = 0
    try:
        holders = int(m.group("holders"))
    except ValueError:
        holders = 0
    try:
        initial_lp = float(m.group("initial_lp"))
    except ValueError:
        initial_lp = 0
    try:
        dev_bal = float(m.group("dev_balance_sol").replace(",", ""))
    except ValueError:
        dev_bal = 0
    return {
        "symbol": m.group("symbol").strip(),
        "name": m.group("name").strip(),
        "ca": m.group("ca"),
        "dex": m.group("dex").strip(),
        "signal_type": "FEATURED_NEW",
        "mcp": mcp,
        "liq_usd": liq_usd,
        "liq_sol": liq_sol,
        "initial_lp_pct": initial_lp,
        "holders": holders,
        "top10_pct": 0,
        "no_mint": False,
        "blacklist_safe": False,
        "burnt": False,
        "dev_status": "UNKNOWN",
        "renounced": "✅" in m.group("renounced"),
        "dev_balance_sol": dev_bal,
        "price_change_5m": 0,
        "price_change_1h": 0,
        "price_change_6h": 0,
        "txns_5m": 0,
        "volume_5m": 0,
        "first_seen": datetime.now(timezone.utc).timestamp(),
        "launch_mcp": mcp,
        "launch_liq": liq_usd,
        "ath_mcp": mcp,
        "ath_multiplier": 1.0,
        "status": "tracking",
        "last_check": datetime.now(timezone.utc).timestamp(),
    }

def load_tracked():
    global _tracked_tokens
    try:
        with open(DATA_FILE) as f:
            _tracked_tokens = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _tracked_tokens = {}

def save_tracked():
    with open(DATA_FILE, "w") as f:
        json.dump(_tracked_tokens, f, indent=2, default=str)

async def get_client():
    if _get_tg_client:
        return await _get_tg_client()
    return None

async def scan_channels(client):
    global _stats
    for cid in CHANNELS:
        try:
            entity = await client.get_entity(cid)
            last_id = _last_msg_ids.get(cid, 0)
            msgs = await client.get_messages(entity, limit=20)
            if not msgs:
                continue
            for msg in msgs:
                if msg.id <= last_id:
                    continue
                if not msg.text:
                    continue

                token = None
                if cid == -1002202241417:
                    token = parse_gmgn_lv2_msg(msg.text)
                    if not token and msg.text and len(msg.text) > 10:
                        logger.info(f"[GMGN-PARSE-FAIL] msg_id={msg.id} text={msg.text[:200]}")
                else:
                    token = parse_gmgn_msg(msg.text)
                    if not token:
                        token = parse_gmgn_lv2_msg(msg.text)

                if not token:
                    continue

                _stats["scanned"] += 1
                ca = token["ca"]
                token["source_channel"] = cid
                token["source_channel_name"] = CHANNEL_NAMES.get(cid, f"Channel {cid}")

                if ca not in _tracked_tokens:
                    _tracked_tokens[ca] = token
                    _stats["new_tokens"] += 1
                    logger.info(f"[NEW] {token['symbol']} MCP=${token['mcp']:.0f} liq=${token['liq_usd']:.0f} holders={token['holders']} signal={token['signal_type']} ch={CHANNEL_NAMES.get(cid, '?')}")
                    # UNIFIED SIGNAL: DexScreener check + multi-source scoring
                    try:
                        from unified_signal import score_token

                        # Step 1: Quick DexScreener health check
                        dex_health = None
                        try:
                            from dex_health import check_token_health
                            from dex_client import DexScreenerClient
                            async with DexScreenerClient() as dex_client:
                                dex_health = await check_token_health(dex_client, ca)
                        except Exception:
                            pass

                        # Step 2: Score with unified system
                        score_result = score_token(token, dex_health)
                        token["unified_score"] = score_result.get("score", 0)
                        token["unified_verdict"] = score_result.get("verdict", "SKIP")
                        token["unified_action"] = score_result.get("action", "IGNORE")
                        token["unified_breakdown"] = score_result.get("breakdown", {})
                        score = score_result["score"]
                        verdict = score_result["verdict"]
                        action = score_result["action"]
                        breakdown = score_result.get("breakdown", {})
                        dex_verified = score_result.get("dex_verified", False)

                        if action in ("BUY_NOW", "ALERT"):
                            launch_mcp = token.get("mcp", 0) or token.get("launch_mcp", 0)
                            dex_status = "✅ DexScreener verified" if dex_verified else "⚠️ No DexScreener check"
                            alert_msg = (
                                f"{'🟢' if action == 'BUY_NOW' else '🟡'} <b>UNIFIED SIGNAL: {verdict}</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏷️ <b>{token['symbol']}</b> ({token.get('signal_type', '?')})\n"
                                f"📍 <code>{ca}</code>\n"
                                f"🎯 Score: <b>{score:.0f}/100</b> ({verdict})\n"
                                f"📊 Early: {breakdown.get('early_detection', 0):.0f} | Winner: {breakdown.get('winner_fit', 0):.0f} | Multi: {breakdown.get('multi_source', 0):.0f} | Fund: {breakdown.get('fundamentals', 0):.0f}\n"
                                f"💰 MCP: ${launch_mcp:,.0f} | Liq: ${token.get('liq_usd', 0):,.0f} | Holders: {token.get('holders', 0)}\n"
                                f"🔬 {dex_status}\n"
                                f"📝 {score_result.get('reason', '')}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🔗 <a href=\"https://gmgn.ai/sol/token/{ca}\">GMGN</a> | "
                                f"<a href=\"https://dexscreener.com/solana/{ca}\">DexScreener</a>"
                            )
                            try:
                                alert_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pending_alert")
                                with open(alert_file, "w") as af:
                                    af.write(alert_msg)
                            except Exception:
                                pass
                            logger.info(f"🚨 UNIFIED SIGNAL: {token['symbol']} score={score:.0f} {verdict} {action} dex={'✅' if dex_verified else '❌'}")
                        elif score >= 50:
                            logger.info(f"👀 WATCH: {token['symbol']} score={score:.0f} {verdict}")
                    except Exception:
                        pass
                    # Record channel signal for channel intelligence
                    try:
                        from learner import record_channel_signal
                        record_channel_signal(
                            channel_id=cid,
                            channel_name=CHANNEL_NAMES.get(cid, f"Channel {cid}"),
                            symbol=token.get("symbol", ""),
                            address=ca,
                            features={
                                "mcp": token.get("mcp", 0),
                                "liq_usd": token.get("liq_usd", 0),
                                "holders": token.get("holders", 0),
                                "initial_lp_pct": token.get("initial_lp_pct", 0),
                                "dev_balance_sol": token.get("dev_balance_sol", 0),
                                "renounced": token.get("renounced", False),
                            },
                            signal_type=token.get("signal_type", ""),
                        )
                    except Exception:
                        pass
                else:
                    existing = _tracked_tokens[ca]
                    existing["last_check"] = datetime.now(timezone.utc).timestamp()
                    if token.get("signal_type") not in ("FEATURED_NEW", ""):
                        existing["signal_type"] = token["signal_type"]
                    if token.get("holders", 0) > 0:
                        existing["holders"] = token["holders"]
                    if token.get("top10_pct", 0) > 0:
                        existing["top10_pct"] = token["top10_pct"]
                    if token.get("dev_status", "UNKNOWN") != "UNKNOWN":
                        existing["dev_status"] = token["dev_status"]
                    # Re-score existing token with unified signal when new data arrives
                    if token.get("holders", 0) > 0 or token.get("liq_usd", 0) > 0:
                        try:
                            from unified_signal import score_token
                            existing["source_channel"] = cid
                            existing["source_channel_name"] = CHANNEL_NAMES.get(cid, f"Channel {cid}")

                            # DexScreener health check
                            dex_health = None
                            try:
                                from dex_health import check_token_health
                                from dex_client import DexScreenerClient
                                async with DexScreenerClient() as dex_client:
                                    dex_health = await check_token_health(dex_client, ca)
                            except Exception:
                                pass

                            score_result = score_token(existing, dex_health)
                            existing["unified_score"] = score_result.get("score", 0)
                            existing["unified_verdict"] = score_result.get("verdict", "SKIP")
                            existing["unified_action"] = score_result.get("action", "IGNORE")
                            score = score_result["score"]
                            action = score_result["action"]
                            dex_verified = score_result.get("dex_verified", False)

                            if action in ("BUY_NOW", "ALERT") and not await self.state.is_alerted(ca):
                                breakdown = score_result.get("breakdown", {})
                                launch_mcp = existing.get("mcp", 0) or existing.get("launch_mcp", 0)
                                dex_status = "✅ DexScreener verified" if dex_verified else "⚠️ No DexScreener check"
                                alert_msg = (
                                    f"{'🟢' if action == 'BUY_NOW' else '🟡'} <b>UNIFIED SIGNAL: {score_result['verdict']}</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🏷️ <b>{existing.get('symbol', '?')}</b> ({existing.get('signal_type', '?')})\n"
                                    f"📍 <code>{ca}</code>\n"
                                    f"🎯 Score: <b>{score:.0f}/100</b> ({score_result['verdict']})\n"
                                    f"📊 Early: {breakdown.get('early_detection', 0):.0f} | Winner: {breakdown.get('winner_fit', 0):.0f} | Multi: {breakdown.get('multi_source', 0):.0f} | Fund: {breakdown.get('fundamentals', 0):.0f}\n"
                                    f"💰 MCP: ${launch_mcp:,.0f} | Liq: ${existing.get('liq_usd', 0):,.0f} | Holders: {existing.get('holders', 0)}\n"
                                    f"🔬 {dex_status}\n"
                                    f"📝 {score_result.get('reason', '')}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🔗 <a href=\"https://gmgn.ai/sol/token/{ca}\">GMGN</a> | "
                                    f"<a href=\"https://dexscreener.com/solana/{ca}\">DexScreener</a>"
                                )
                                try:
                                    alert_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pending_alert")
                                    with open(alert_file, "w") as af:
                                        af.write(alert_msg)
                                except Exception:
                                    pass
                                logger.info(f"🚨 UNIFIED RE-SCORE: {existing.get('symbol', '?')} score={score:.0f} {score_result['verdict']} {action} dex={'✅' if dex_verified else '❌'}")
                        except Exception:
                            pass
            _last_msg_ids[cid] = msgs[0].id
        except Exception as e:
            _stats["errors"] += 1
            logger.debug(f"Channel {cid} scan error: {e}")

async def check_ath(dex_client, address: str) -> tuple:
    if address in _ath_check_cache:
        last_check, result = _ath_check_cache[address]
        if datetime.now(timezone.utc).timestamp() - last_check < 300:
            return result
    try:
        pair = await asyncio.wait_for(dex_client.fetch_pair_data(address), timeout=10)
        if not pair:
            return None, None
        price_str = pair.get("priceUsd", "0")
        current_price = float(price_str) if price_str else 0
        fdv = float(pair.get("fdv", 0) or 0)
        _ath_check_cache[address] = (datetime.now(timezone.utc).timestamp(), (current_price, fdv))
        return current_price, fdv
    except:
        return None, None

async def run_ath_checks(dex_client):
    now = datetime.now(timezone.utc).timestamp()
    checked = 0
    for ca, token in list(_tracked_tokens.items()):
        if checked >= 20:
            break
        if now - token.get("last_check", 0) > 300:
            checked += 1
            current_price, current_mcp = await check_ath(dex_client, ca)
            if current_mcp and current_mcp > 0:
                token["last_check"] = now

                if token.get("launch_mcp", 0) <= 0:
                    token["launch_mcp"] = current_mcp

                if current_mcp > token.get("ath_mcp", 0):
                    token["ath_mcp"] = current_mcp

                launch = token.get("launch_mcp", 0)
                if launch > 0:
                    token["ath_multiplier"] = token["ath_mcp"] / launch
                    multiplier = token["ath_multiplier"]
                    if multiplier >= 50:
                        if token.get("status") != "mega_winner":
                            token["status"] = "mega_winner"
                            logger.info(f"[MEGA WINNER] {token['symbol']} x{multiplier:.0f} (${launch:.0f}→${current_mcp:.0f})")
                            _sync_winner(token)
                            _update_channel_outcome(token, multiplier, "MEGA_PUMP")
                    elif multiplier >= 5:
                        if token.get("status") not in ("mega_winner", "winner"):
                            token["status"] = "winner"
                            logger.info(f"[WINNER] {token['symbol']} x{multiplier:.1f}")
                            _sync_winner(token)
                            _update_channel_outcome(token, multiplier, "PUMP")
                    elif current_mcp < launch * 0.3 and token.get("status") == "tracking":
                        token["status"] = "loser"
                        _update_channel_outcome(token, multiplier, "DUMP")

async def run_loop(dex_client, interval: int = 15):
    logger.info("Starting Telegram collector loop...")
    client = await get_client()
    if not client:
        logger.error("Cannot start collector - no client")
        return
    load_tracked()
    while True:
        try:
            await scan_channels(client)
            await run_ath_checks(dex_client)
            save_tracked()
            tracked = len(_tracked_tokens)
            winners = sum(1 for t in _tracked_tokens.values() if t.get("status") in ("winner", "mega_winner"))
            losers = sum(1 for t in _tracked_tokens.values() if t.get("status") == "loser")
            if tracked > 0:
                logger.info(f"[COLLECTOR] {tracked} tracked | {winners} winners | {losers} losers | {_stats['new_tokens']} new this session")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Collector error: {e}")
        await asyncio.sleep(interval)

def get_tracked_tokens():
    return _tracked_tokens

def get_winners(min_mult: float = 5.0):
    return {ca: t for ca, t in _tracked_tokens.items() if t.get("ath_multiplier", 0) >= min_mult}

def get_stats():
    return {
        "tracked": len(_tracked_tokens),
        "winners": sum(1 for t in _tracked_tokens.values() if t.get("status") in ("winner", "mega_winner")),
        "losers": sum(1 for t in _tracked_tokens.values() if t.get("status") == "loser"),
        "new_this_session": _stats["new_tokens"],
        "scanned": _stats["scanned"],
    }

def find_daily_best():
    """Analyze all tracked tokens and find best performers with patterns."""
    now = datetime.now(timezone.utc).timestamp()
    day_ago = now - 86400

    recent = []
    for ca, t in _tracked_tokens.items():
        first_seen = t.get("first_seen", 0)
        if first_seen >= day_ago or t.get("ath_multiplier", 0) >= 2.0:
            recent.append(t)

    if not recent:
        recent = list(_tracked_tokens.values())

    recent.sort(key=lambda x: x.get("ath_multiplier", 0), reverse=True)

    holder_ranges = {"1-5": [], "6-10": [], "11-20": [], "21-50": [], "50+": []}
    dev_ranges = {"0-0.5": [], "0.5-1": [], "1-2": [], "2-5": [], "5+": []}
    liq_ranges = {"0-5K": [], "5-10K": [], "10-20K": [], "20-50K": [], "50K+": []}
    mcap_ranges = {"0-5K": [], "5-10K": [], "10-20K": [], "20-50K": [], "50K+": []}
    signal_types = defaultdict(list)
    channel_stats = defaultdict(list)

    for t in recent:
        ath = t.get("ath_multiplier", 1)
        is_winner = ath >= 5

        holders = t.get("holders", 0)
        if 1 <= holders <= 5:
            holder_ranges["1-5"].append(is_winner)
        elif 6 <= holders <= 10:
            holder_ranges["6-10"].append(is_winner)
        elif 11 <= holders <= 20:
            holder_ranges["11-20"].append(is_winner)
        elif 21 <= holders <= 50:
            holder_ranges["21-50"].append(is_winner)
        elif holders > 50:
            holder_ranges["50+"].append(is_winner)

        dev_bal = t.get("dev_balance_sol", 0)
        if 0 < dev_bal <= 0.5:
            dev_ranges["0-0.5"].append(is_winner)
        elif 0.5 < dev_bal <= 1:
            dev_ranges["0.5-1"].append(is_winner)
        elif 1 < dev_bal <= 2:
            dev_ranges["1-2"].append(is_winner)
        elif 2 < dev_bal <= 5:
            dev_ranges["2-5"].append(is_winner)
        elif dev_bal > 5:
            dev_ranges["5+"].append(is_winner)

        liq = t.get("launch_liq", t.get("liq_usd", 0))
        if 0 < liq <= 5000:
            liq_ranges["0-5K"].append(is_winner)
        elif 5000 < liq <= 10000:
            liq_ranges["5-10K"].append(is_winner)
        elif 10000 < liq <= 20000:
            liq_ranges["10-20K"].append(is_winner)
        elif 20000 < liq <= 50000:
            liq_ranges["20-50K"].append(is_winner)
        elif liq > 50000:
            liq_ranges["50K+"].append(is_winner)

        mcap = t.get("launch_mcp", t.get("mcp", 0))
        if 0 < mcap <= 5000:
            mcap_ranges["0-5K"].append(is_winner)
        elif 5000 < mcap <= 10000:
            mcap_ranges["5-10K"].append(is_winner)
        elif 10000 < mcap <= 20000:
            mcap_ranges["10-20K"].append(is_winner)
        elif 20000 < mcap <= 50000:
            mcap_ranges["20-50K"].append(is_winner)
        elif mcap > 50000:
            mcap_ranges["50K+"].append(is_winner)

        sig = t.get("signal_type", "UNKNOWN")
        signal_types[sig].append(is_winner)

        ch = t.get("source_channel", 0)
        channel_stats[ch].append(is_winner)

    def win_rate(lst):
        if not lst:
            return 0, 0, 0
        wins = sum(1 for x in lst if x)
        return round(wins / len(lst) * 100, 1), wins, len(lst)

    patterns = {}
    for name, lst in [("holders", holder_ranges), ("dev_sol", dev_ranges),
                      ("liquidity", liq_ranges), ("mcap", mcap_ranges)]:
        patterns[name] = {}
        for rng, outcomes in lst.items():
            wr, w, n = win_rate(outcomes)
            patterns[name][rng] = {"win_rate": wr, "winners": w, "total": n}

    patterns["signal_type"] = {}
    for sig, outcomes in signal_types.items():
        wr, w, n = win_rate(outcomes)
        patterns["signal_type"][sig] = {"win_rate": wr, "winners": w, "total": n}

    patterns["channel"] = {}
    for ch, outcomes in channel_stats.items():
        wr, w, n = win_rate(outcomes)
        ch_name = CHANNEL_NAMES.get(ch, f"Channel {ch}")
        patterns["channel"][ch_name] = {"win_rate": wr, "winners": w, "total": n}

    top10 = recent[:10]
    best_combos = []
    combo_data = defaultdict(list)
    for t in recent:
        ath = t.get("ath_multiplier", 1)
        is_winner = ath >= 5
        holders = t.get("holders", 0)
        dev = t.get("dev_balance_sol", 0)
        sig = t.get("signal_type", "UNKNOWN")

        if holders <= 7 and dev < 1:
            combo_data["Low holders + Low dev"].append(is_winner)
        if sig == "KOTH":
            combo_data["KOTH signal"].append(is_winner)
        if sig == "KOL_FOMO":
            combo_data["KOL FOMO signal"].append(is_winner)
        if holders <= 5 and dev < 0.5:
            combo_data["≤5 holders + <0.5 SOL dev"].append(is_winner)
        if t.get("no_mint") and t.get("blacklist_safe"):
            combo_data["NoMint + Blacklist safe"].append(is_winner)
        if t.get("dev_status") == "SELL_ALL":
            combo_data["Dev selling"].append(is_winner)

    for combo_name, outcomes in combo_data.items():
        wr, w, n = win_rate(outcomes)
        if n >= 3:
            best_combos.append({"pattern": combo_name, "win_rate": wr, "winners": w, "total": n})
    best_combos.sort(key=lambda x: x["win_rate"], reverse=True)

    try:
        with open(BOT_DATA_FILE) as f:
            data = json.load(f)
        data.setdefault("model", {})["channel_insights"] = {
            "patterns": patterns,
            "best_combos": best_combos[:10],
            "top_coins": [{"symbol": t.get("symbol"), "ath": t.get("ath_multiplier", 0),
                           "channel": CHANNEL_NAMES.get(t.get("source_channel", 0), "?"),
                           "signal_type": t.get("signal_type", "?")} for t in top10],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(BOT_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Save channel_insights error: {e}")

    return {
        "top10": top10,
        "patterns": patterns,
        "best_combos": best_combos[:10],
        "total_analyzed": len(recent),
    }

def learn_channel_patterns():
    """Learn patterns from all tracked tokens across 5 channels."""
    insights = {"holder_ranges": {}, "dev_ranges": {}, "liq_ranges": {}, "signal_types": {}, "channels": {}}

    holder_bins = {"1-5": [0, 0], "6-10": [0, 0], "11-20": [0, 0], "21-50": [0, 0], "50+": [0, 0]}
    dev_bins = {"0-0.5": [0, 0], "0.5-1": [0, 0], "1-2": [0, 0], "2-5": [0, 0], "5+": [0, 0]}
    liq_bins = {"0-5K": [0, 0], "5-10K": [0, 0], "10-20K": [0, 0], "20-50K": [0, 0], "50K+": [0, 0]}
    sig_bins = defaultdict(lambda: [0, 0])
    ch_bins = defaultdict(lambda: [0, 0])

    for ca, t in _tracked_tokens.items():
        ath = t.get("ath_multiplier", 1)
        is_winner = ath >= 5

        holders = t.get("holders", 0)
        if 1 <= holders <= 5:
            holder_bins["1-5"][0] += 1
            holder_bins["1-5"][1] += int(is_winner)
        elif 6 <= holders <= 10:
            holder_bins["6-10"][0] += 1
            holder_bins["6-10"][1] += int(is_winner)
        elif 11 <= holders <= 20:
            holder_bins["11-20"][0] += 1
            holder_bins["11-20"][1] += int(is_winner)
        elif 21 <= holders <= 50:
            holder_bins["21-50"][0] += 1
            holder_bins["21-50"][1] += int(is_winner)
        elif holders > 50:
            holder_bins["50+"][0] += 1
            holder_bins["50+"][1] += int(is_winner)

        dev = t.get("dev_balance_sol", 0)
        if 0 < dev <= 0.5:
            dev_bins["0-0.5"][0] += 1; dev_bins["0-0.5"][1] += int(is_winner)
        elif 0.5 < dev <= 1:
            dev_bins["0.5-1"][0] += 1; dev_bins["0.5-1"][1] += int(is_winner)
        elif 1 < dev <= 2:
            dev_bins["1-2"][0] += 1; dev_bins["1-2"][1] += int(is_winner)
        elif 2 < dev <= 5:
            dev_bins["2-5"][0] += 1; dev_bins["2-5"][1] += int(is_winner)
        elif dev > 5:
            dev_bins["5+"][0] += 1; dev_bins["5+"][1] += int(is_winner)

        liq = t.get("launch_liq", t.get("liq_usd", 0))
        if 0 < liq <= 5000:
            liq_bins["0-5K"][0] += 1; liq_bins["0-5K"][1] += int(is_winner)
        elif 5000 < liq <= 10000:
            liq_bins["5-10K"][0] += 1; liq_bins["5-10K"][1] += int(is_winner)
        elif 10000 < liq <= 20000:
            liq_bins["10-20K"][0] += 1; liq_bins["10-20K"][1] += int(is_winner)
        elif 20000 < liq <= 50000:
            liq_bins["20-50K"][0] += 1; liq_bins["20-50K"][1] += int(is_winner)
        elif liq > 50000:
            liq_bins["50K+"][0] += 1; liq_bins["50K+"][1] += int(is_winner)

        sig = t.get("signal_type", "UNKNOWN")
        sig_bins[sig][0] += 1
        sig_bins[sig][1] += int(is_winner)

        ch = t.get("source_channel_name", "Unknown")
        ch_bins[ch][0] += 1
        ch_bins[ch][1] += int(is_winner)

    for bins, key in [(holder_bins, "holder_ranges"), (dev_bins, "dev_ranges"),
                      (liq_bins, "liq_ranges")]:
        insights[key] = {}
        for rng, (total, wins) in bins.items():
            insights[key][rng] = {"total": total, "winners": wins,
                                  "win_rate": round(wins / max(total, 1) * 100, 1)}

    insights["signal_types"] = {}
    for sig, (total, wins) in sig_bins.items():
        insights["signal_types"][sig] = {"total": total, "winners": wins,
                                         "win_rate": round(wins / max(total, 1) * 100, 1)}

    insights["channels"] = {}
    for ch, (total, wins) in ch_bins.items():
        insights["channels"][ch] = {"total": total, "winners": wins,
                                    "win_rate": round(wins / max(total, 1) * 100, 1)}

    try:
        with open(BOT_DATA_FILE) as f:
            data = json.load(f)
        data.setdefault("model", {})["learned_patterns"] = insights
        with open(BOT_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Save learned_patterns error: {e}")

    return insights

def _sync_winner(token: dict):
    try:
        ca = token["ca"]
        with open(BOT_DATA_FILE) as f:
            data = json.load(f)
        sr = data.setdefault("model", {}).setdefault("signal_results", [])
        for entry in sr:
            if entry.get("address") == ca:
                return
        now_str = datetime.now(timezone.utc).isoformat()
        ath = round(token["ath_multiplier"], 2)
        sr.append({
            "address": ca,
            "symbol": token["symbol"],
            "name": token["name"],
            "ath_multiplier": ath,
            "current_multiplier": ath,
            "min_price_multiplier": 1.0,
            "launch_mcap": token.get("launch_mcp", 0),
            "peak_mcap": token.get("ath_mcp", 0),
            "launch_liq": token.get("launch_liq", 0),
            "holders": token.get("holders", 0),
            "signal_type": token.get("signal_type", "UNKNOWN"),
            "source_channel": token.get("source_channel_name", "Unknown"),
            "top10_pct": token.get("top10_pct", 0),
            "no_mint": token.get("no_mint", False),
            "blacklist_safe": token.get("blacklist_safe", False),
            "dev_status": token.get("dev_status", "UNKNOWN"),
            "is_pump": True,
            "score": 0.9 if ath >= 50 else 0.75,
            "verdict": "MEGA_PUMP" if ath >= 50 else ("STRONG_PUMP" if ath >= 5 else "PUMP"),
            "timestamp": now_str,
            "detected_at": now_str,
            "source": "collector_sync",
        })
        with open(BOT_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[SYNC] Added {token['symbol']} x{token['ath_multiplier']:.1f} from {token.get('source_channel_name', '?')}")
    except Exception as e:
        logger.debug(f"Sync winner error: {e}")


def _update_channel_outcome(token: dict, ath_multiplier: float, verdict: str):
    """Update channel stats when a token outcome is determined."""
    try:
        ch_id = token.get("source_channel")
        if not ch_id:
            return
        from learner import update_channel_outcome
        update_channel_outcome(
            channel_id=ch_id,
            address=token.get("ca", ""),
            ath_multiplier=ath_multiplier,
            current_multiplier=ath_multiplier,
            verdict=verdict,
        )
    except Exception:
        pass
