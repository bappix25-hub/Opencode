import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from bot_state import BotState
from learner import get_stats, get_daily_report, learn_pump, learn_dump, is_duplicate, verify_pump
from dex_client import DexScreenerClient
from config import config
from utils import format_number

logger = logging.getLogger("telegram_bot")

def main_keyboard():
    keyboard = [
        [KeyboardButton("📊 স্ট্যাটাস"), KeyboardButton("📈 পারফরম্যান্স")],
        [KeyboardButton("🏆 ট্রেন"), KeyboardButton("⚙️ সেটিংস")],
        [KeyboardButton("✅ অন"), KeyboardButton("❌ অফ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

class TelegramHandlers:
    def __init__(self, state: BotState, dex: DexScreenerClient, session):
        self.state = state
        self.dex = dex
        self.session = session

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 <b>Bappis Trade Bot v2 চালু!</b>\n"
            "AI-powered Solana মেমে কয়েন ট্র্যাকার\n\n"
            "📚 কমান্ড:\n"
            "/pump ADDRESS — পাম্প শেখান\n"
            "/dump ADDRESS — ডাম্প শেখান\n"
            "/forcepump ADDRESS — ফোর্স পাম্প\n"
            "/threshold 50 — থ্রেশোল্ড সেট (১-১০০)\n"
            "/health — বটের স্বাস্থ্য\n"
            "/config — কনফিগারেশন",
            parse_mode="HTML", reply_markup=main_keyboard()
        )

    async def cmd_threshold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        current = await self.state.get_threshold()
        if not context.args:
            await update.message.reply_text(
                f"⚙️ বর্তমান থ্রেশোল্ড: <b>{int(current*100)}%</b>",
                parse_mode="HTML"
            )
            return
        try:
            val = int(context.args[0])
            if not 1 <= val <= 100:
                await update.message.reply_text("❌ ১-১০০ এর মধ্যে দিন।")
                return
            await self.state.set_threshold(val / 100)
            await update.message.reply_text(f"✅ থ্রেশোল্ড: <b>{val}%</b>", parse_mode="HTML")
        except (ValueError, IndexError):
            await update.message.reply_text("❌ যেমন: /threshold 50")

    async def cmd_pump(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ /pump TOKEN_ADDRESS")
            return
        address = context.args[0].strip()
        if is_duplicate(address):
            await update.message.reply_text("⚠️ ডুপ্লিকেট!")
            return
        await update.message.reply_text("⏳ ডেটা আনছি...")
        pair = await self.dex.fetch_pair_data(address)
        if not pair:
            await update.message.reply_text("❌ ডেটা পাওয়া যায়নি।")
            return
        name = pair.get("baseToken", {}).get("name", "Unknown")
        symbol = pair.get("baseToken", {}).get("symbol", "???")
        coin_info = {"name": name, "symbol": symbol}
        verified, actual_multi = verify_pump(pair, config.pump_multiplier)
        if not verified:
            await update.message.reply_text(
                f"⚠️ {config.pump_multiplier}x ভেরিফাই হয়নি ({actual_multi}x)\n/forcepump {address}",
                parse_mode="HTML"
            )
            return
        ok, msg = learn_pump(coin_info, pair, actual_multi, address, manual=True)
        await update.message.reply_text(f"{'✅' if ok else '❌'} <b>{name}</b>\n{msg}", parse_mode="HTML")

    async def cmd_forcepump(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ /forcepump TOKEN_ADDRESS")
            return
        address = context.args[0].strip()
        if is_duplicate(address):
            await update.message.reply_text("⚠️ ডুপ্লিকেট!")
            return
        await update.message.reply_text("⏳ ডেটা আনছি...")
        pair = await self.dex.fetch_pair_data(address)
        if not pair:
            await update.message.reply_text("❌ ডেটা পাওয়া যায়নি।")
            return
        name = pair.get("baseToken", {}).get("name", "Unknown")
        symbol = pair.get("baseToken", {}).get("symbol", "???")
        coin_info = {"name": name, "symbol": symbol}
        ok, msg = learn_pump(coin_info, pair, config.pump_multiplier, address, manual=True)
        await update.message.reply_text(f"{'✅' if ok else '❌'} <b>{name}</b>\n{msg}", parse_mode="HTML")

    async def cmd_dump(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ /dump TOKEN_ADDRESS")
            return
        address = context.args[0].strip()
        if is_duplicate(address):
            await update.message.reply_text("⚠️ ডুপ্লিকেট!")
            return
        await update.message.reply_text("⏳ ডেটা আনছি...")
        pair = await self.dex.fetch_pair_data(address)
        if not pair:
            await update.message.reply_text("❌ ডেটা পাওয়া যায়নি।")
            return
        name = pair.get("baseToken", {}).get("name", "Unknown")
        symbol = pair.get("baseToken", {}).get("symbol", "???")
        coin_info = {"name": name, "symbol": symbol}
        ok, msg = learn_dump(coin_info, pair, address, manual=True)
        await update.message.reply_text(f"{'✅' if ok else '❌'} <b>{name}</b>\n{msg}", parse_mode="HTML")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = await self.state.get_stats()
        learner_stats = get_stats()
        active = "🟢 চালু" if stats["bot_active"] else "🔴 বন্ধ"
        await update.message.reply_text(
            f"🏥 <b>বট স্বাস্থ্য</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"অবস্থা: {active}\n"
            f"🆕 লঞ্চ ট্র্যাক: <b>{stats['launch_tracking']}</b>\n"
            f"🔍 মাইগ্রেশন ট্র্যাক: <b>{stats['tracked_coins']}</b>\n"
            f"🚫 ব্ল্যাকলিস্ট: <b>{stats['blacklisted']}</b>\n"
            f"🚀 পাম্প: <b>{stats['pump_coins']}</b>\n"
            f"📚 শেখা প্যাটার্ন: <b>{learner_stats['pump_patterns']}</b>\n"
            f"⚡ সিগন্যাল পাঠানো: <b>{learner_stats['total_signals']}</b>\n"
            f"✅ সফলতা: <b>{learner_stats['accuracy']}%</b>\n"
            f"🎯 থ্রেশোল্ড: <b>{int(stats['current_threshold']*100)}%</b>",
            parse_mode="HTML"
        )

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"⚙️ <b>কনফিগারেশন</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📈 পাম্প মাল্টিপ্লায়ার: <b>{config.pump_multiplier}x</b>\n"
            f"🎯 AI থ্রেশোল্ড: <b>{int(config.ai_threshold*100)}%</b>\n"
            f"💧 মিন লিকুইডিটি: <b>{format_number(config.min_liquidity)}</b>\n"
            f"💰 MCap: {format_number(config.min_mcap)} - {format_number(config.max_mcap)}\n"
            f"⏱️ স্ক্যান ইন্টারভাল: <b>{config.scan_interval}s</b>\n"
            f"🔄 প্রি-মাইগ্রেশন: <b>{'চালু' if config.enable_pre_migration else 'বন্ধ'}</b>",
            parse_mode="HTML"
        )

    async def handle_buttons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        if text == "📊 স্ট্যাটাস":
            stats = await self.state.get_stats()
            learner_stats = get_stats()
            status = "🟢 চালু" if stats["bot_active"] else "🔴 বন্ধ"
            await update.message.reply_text(
                f"📊 <b>বটের অবস্থা: {status}</b>\n"
                f"🆕 লঞ্চ ট্র্যাক: <b>{stats['launch_tracking']}</b>\n"
                f"🔍 মাইগ্রেশন ট্র্যাক: <b>{stats['tracked_coins']}</b>\n"
                f"🚫 ব্ল্যাকলিস্ট: <b>{stats['blacklisted']}</b>\n"
                f"🚀 পাম্প: <b>{stats['pump_coins']}</b>\n"
                f"🧠 পাম্প প্যাটার্ন: <b>{learner_stats['pump_patterns']}</b>\n"
                f"📚 লঞ্চ প্যাটার্ন: <b>{learner_stats['launch_patterns']}</b>\n"
                f"📉 ডাম্প প্যাটার্ন: <b>{learner_stats['dump_patterns']}</b>\n"
                f"🎯 থ্রেশোল্ড: <b>{int(stats['current_threshold']*100)}%</b>",
                parse_mode="HTML"
            )
        elif text == "📈 পারফরম্যান্স":
            learner_stats = get_stats()
            await update.message.reply_text(
                f"📈 <b>পারফরম্যান্স</b>\n"
                f"⚡ মোট সিগন্যাল: <b>{learner_stats['total_signals']}</b>\n"
                f"✅ চেক হয়েছে: <b>{learner_stats['checked_signals']}</b>\n"
                f"🏆 সফল (2x+): <b>{learner_stats['successful_signals']}</b>\n"
                f"🎯 একুরেসি: <b>{learner_stats['accuracy']}%</b>\n"
                f"⏰ সেরা সময়: <b>{learner_stats['best_hour']}:00 UTC</b>",
                parse_mode="HTML"
            )
        elif text == "🏆 ট্রেন":
            learner_stats = get_stats()
            await update.message.reply_text(
                f"🏆 <b>লার্নিং স্ট্যাটাস</b>\n"
                f"🧠 পাম্প প্যাটার্ন: <b>{learner_stats['pump_patterns']}</b>\n"
                f"📚 লঞ্চ প্যাটার্ন: <b>{learner_stats['launch_patterns']}</b>\n"
                f"📉 ডাম্প প্যাটার্ন: <b>{learner_stats['dump_patterns']}</b>\n"
                f"✍️ ম্যানুয়াল পাম্প: <b>{learner_stats['manual_pumps']}</b>\n"
                f"🎯 থ্রেশোল্ড: <b>{int(learner_stats['threshold']*100)}%</b>\n"
                f"📊 একুরেসি: <b>{learner_stats['accuracy']}%</b>\n"
                f"\n/pump ADDRESS\n/dump ADDRESS\n/threshold 50",
                parse_mode="HTML"
            )
        elif text == "⚙️ সেটিংস":
            await self.cmd_config(update, context)
        elif text == "✅ অন":
            await self.state.set_bot_active(True)
            await update.message.reply_text("✅ বট চালু!")
        elif text == "❌ অফ":
            await self.state.set_bot_active(False)
            await update.message.reply_text("❌ বট বন্ধ!")


def register_handlers(app, handlers: TelegramHandlers):
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("pump", handlers.cmd_pump))
    app.add_handler(CommandHandler("dump", handlers.cmd_dump))
    app.add_handler(CommandHandler("forcepump", handlers.cmd_forcepump))
    app.add_handler(CommandHandler("threshold", handlers.cmd_threshold))
    app.add_handler(CommandHandler("health", handlers.cmd_health))
    app.add_handler(CommandHandler("config", handlers.cmd_config))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_buttons))
