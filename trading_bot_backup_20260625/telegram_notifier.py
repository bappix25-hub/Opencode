import asyncio
import logging
from typing import Optional
from config import config
from utils import format_usd, format_sol, format_pct

logger = logging.getLogger("telegram")

GMGN_TOKEN_URL = "https://gmgn.ai/sol/token/{address}"
GMGN_TRADE_URL = "https://gmgn.ai/sol/token/{address}?ref={ref}"
DEXSCREENER_URL = "https://dexscreener.com/solana/{address}"


def get_gmgn_link(address: str, ref: str = "tradingbot") -> str:
    return GMGN_TRADE_URL.format(address=address, ref=ref)


def get_dexscreener_link(address: str) -> str:
    return DEXSCREENER_URL.format(address=address)


def get_links(address: str) -> str:
    if not address or len(address) < 10:
        return ""
    gmgn = get_gmgn_link(address)
    dex = get_dexscreener_link(address)
    return f'\n🔗 GMGN: {gmgn}\n🔗 DexScreener: {dex}'


def get_main_keyboard():
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    keyboard = [
        [
            KeyboardButton("🟢 Start"),
            KeyboardButton("🔴 Stop"),
        ],
        [
            KeyboardButton("📊 Status"),
            KeyboardButton("🚨 Signals"),
        ],
        [
            KeyboardButton("⚙️ Config"),
            KeyboardButton("🔄 Restart"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_confirm_keyboard():
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    keyboard = [
        [
            KeyboardButton("✅ Yes"),
            KeyboardButton("❌ No"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


class TelegramNotifier:
    def __init__(self):
        self.bot_token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)
        self.bot = None

        if self.enabled:
            try:
                from telegram import Bot
                self.bot = Bot(token=self.bot_token)
                logger.info("Telegram notifications enabled")
            except Exception as e:
                logger.error(f"Telegram init error: {e}")
                self.enabled = False
        else:
            logger.warning("Telegram notifications disabled (no token/chat_id)")

    async def send(self, message: str, parse_mode: str = "HTML", keyboard=None, disable_preview: bool = False) -> bool:
        if not self.enabled or not self.bot:
            logger.info(f"TG: {message[:100]}...")
            return True

        try:
            kwargs = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            }
            if keyboard:
                kwargs["reply_markup"] = keyboard

            await self.bot.send_message(**kwargs)
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def send_with_keyboard(self, message: str, parse_mode: str = "HTML") -> bool:
        return await self.send(message, parse_mode, get_main_keyboard())

    async def notify_pump_detected(self, symbol: str, score: float, signals: list, price: float, address: str = ""):
        links = get_links(address) if address else ""

        msg = (
            f"🎯 <b>PUMP DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Token: <b>${symbol}</b>\n"
            f"Score: <b>{score:.2f}</b>\n"
            f"Price: <b>{format_usd(price)}</b>\n"
            f"Signals: {', '.join(signals)}"
            f"{links}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ Executing buy..."
        )
        await self.send(msg)

    async def notify_signal(self, symbol: str, score: float, signals: list, price: float, address: str = "",
                             mcap: float = 0.0, tp_price: float = 0.0, sl_price: float = 0.0, risk_score: float = 0.0,
                             unique_wallets: int = 0, liquidity: float = 0.0, prob_100k: float = 0.0):
        links = get_links(address) if address else ""

        tp_pct = ((tp_price - price) / price * 100) if price > 0 else 0
        sl_pct = ((sl_price - price) / price * 100) if price > 0 else 0

        risk_text = ""
        if risk_score < -0.2:
            risk_text = "Low Risk"
        elif risk_score < 0.2:
            risk_text = "Medium Risk"
        else:
            risk_text = "High Risk"

        prob_text = ""
        if prob_100k > 0:
            if prob_100k >= 0.6:
                prob_text = f"🎯 $100K MC Prob: {prob_100k:.0%} (HIGH)"
            elif prob_100k >= 0.4:
                prob_text = f"📊 $100K MC Prob: {prob_100k:.0%} (MEDIUM)"
            else:
                prob_text = f"⚠️ $100K MC Prob: {prob_100k:.0%} (LOW)"

        lp_warning = ""
        if unique_wallets <= 3:
            lp_warning = f"\nWARNING: Only {unique_wallets} wallets! Possible single LP provider"
        elif liquidity > 0 and mcap > 0 and mcap / liquidity > 50:
            lp_warning = f"\nWARNING: MC {mcap/liquidity:.0f}x higher than LP - liquidity may be fake"

        msg = (
            f"🚨 SIGNAL 🚨\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Token: ${symbol}\n"
            f"Score: {score:.2f} | Risk: {risk_text}\n"
            f"Price: {format_usd(price)}\n"
            f"MCap: {format_usd(mcap)}\n"
            f"Liq: {format_usd(liquidity)} | Wallets: {unique_wallets}\n"
            f"{prob_text}\n"
            f"Signals: {', '.join(signals)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"TP: {tp_pct:+.0f}% ({format_usd(tp_price)})\n"
            f"SL: {sl_pct:+.0f}% ({format_usd(sl_price)})\n"
            f"{links}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"CHECK: LP Count, Lock% & Top 10% on GMGN\n"
            f"If LP Count = 1 or Lock less than 50% = SKIP"
        )
        if lp_warning:
            msg += lp_warning
        await self.send(msg, disable_preview=True)

    async def notify_buy(self, symbol: str, sol_amount: float, price: float, paper: bool = False, address: str = "",
                         entry_mcap: float = 0.0, tp_mcap: float = 0.0, sl_mcap: float = 0.0):
        mode = "📄 PAPER" if paper else "💰 REAL"
        links = get_links(address) if address else ""

        mcap_line = ""
        if entry_mcap > 0:
            mcap_line = f"\n💰 Market Cap: <b>{format_usd(entry_mcap)}</b>"
        if tp_mcap > 0:
            mcap_line += f"\n🎯 TP Mcap: <b>{format_usd(tp_mcap)}</b>"
        if sl_mcap > 0:
            mcap_line += f"\n🛑 SL Mcap: <b>{format_usd(sl_mcap)}</b>"

        msg = (
            f"🟢 <b>{mode} BUY</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Token: <b>${symbol}</b>\n"
            f"Amount: <b>{format_sol(sol_amount)}</b>\n"
            f"Price: <b>{format_usd(price)}</b>"
            f"{mcap_line}"
            f"{links}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏳ Monitoring for exit..."
        )
        await self.send(msg, keyboard=get_main_keyboard(), disable_preview=True)

    async def notify_sell(self, symbol: str, pnl_sol: float, pnl_pct: float, reason: str, paper: bool = False, address: str = "",
                          entry_mcap: float = 0.0, exit_mcap: float = 0.0):
        emoji = "✅" if pnl_sol >= 0 else "❌"
        mode = "📄 PAPER" if paper else "💰 REAL"
        links = get_links(address) if address else ""

        mcap_line = ""
        if entry_mcap > 0:
            mcap_line = f"\n💰 Entry Mcap: <b>{format_usd(entry_mcap)}</b>"
        if exit_mcap > 0:
            mcap_line += f"\n📤 Exit Mcap: <b>{format_usd(exit_mcap)}</b>"

        msg = (
            f"{emoji} <b>{mode} SELL</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Token: <b>${symbol}</b>\n"
            f"PnL: <b>{format_sol(pnl_sol)} ({format_pct(pnl_pct)})</b>\n"
            f"Reason: <b>{reason}</b>"
            f"{mcap_line}"
            f"{links}\n"
            f"━━━━━━━━━━━━━━━━"
        )
        await self.send(msg, keyboard=get_main_keyboard(), disable_preview=True)

    async def notify_status(self, message: str):
        msg = f"📊 <b>STATUS</b>\n{message}"
        await self.send(msg, keyboard=get_main_keyboard())

    async def notify_start(self, paper_mode: bool):
        mode = "📄 PAPER" if paper_mode else "💰 REAL"
        msg = (
            f"🤖 <b>Trading Bot Started</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Mode: <b>{mode}</b>\n"
            f"Monitoring: Solana meme coins\n"
            f"Strategy: Pump detection + Auto trade\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Commands:</b>\n"
            f"🟢 Start - Bot চালু করো\n"
            f"🔴 Stop - Bot বন্ধ করো\n"
            f"📊 Status - অবস্থা দেখো\n"
            f"📈 Trades - ট্রেড হিস্ট্রি\n"
            f"💰 Balance - ব্যালেন্স\n"
            f"📦 Position - ওপেন পজিশন\n"
            f"⚙️ Config - সেটিংস\n"
            f"🔄 Restart - রিস্টার্ট"
        )
        await self.send(msg, keyboard=get_main_keyboard())

    async def notify_monitoring_update(self, symbol: str, price: float, change_5m: float, volume_5m: float, address: str = ""):
        links = get_links(address) if address else ""

        emoji = "📈" if change_5m > 0 else "📉"
        msg = (
            f"{emoji} <b>MONITOR: ${symbol}</b>\n"
            f"Price: <b>{format_usd(price)}</b>\n"
            f"5m Change: <b>{change_5m:+.1f}%</b>\n"
            f"5m Vol: <b>{format_usd(volume_5m)}</b>"
            f"{links}"
        )
        await self.send(msg)

    async def notify_trade_opportunity(self, symbol: str, score: float, reason: str, address: str = ""):
        links = get_links(address) if address else ""

        msg = (
            f"💡 <b>OPPORTUNITY</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Token: <b>${symbol}</b>\n"
            f"Score: <b>{score:.2f}</b>\n"
            f"Reason: {reason}"
            f"{links}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ Auto-buying..."
        )
        await self.send(msg)

    async def notify_error(self, error_msg: str):
        msg = (
            f"⚠️ <b>ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{error_msg}\n"
            f"━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    async def notify_health(self, status: str):
        msg = f"❤️ <b>Health</b>\n{status}"
        await self.send(msg)
