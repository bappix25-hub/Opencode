import logging
import os
import json
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from bot_state import BotState
from learner import get_stats, get_daily_report, learn_pump, learn_dump, is_duplicate, verify_pump
from dex_client import DexScreenerClient
from helius_client import HeliusClient
from config import config
from utils import format_number
from backtest import BacktestEngine, REPORTS_DIR

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
            "/config — কনফিগারেশন\n"
            "/backtest 30 — ৩০ দিনের backtest\n"
            "/lastbacktest — শেষ backtest দেখাও",
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

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        days = 30
        max_tokens = 300
        if context.args:
            try:
                days = int(context.args[0])
                days = max(1, min(90, days))
            except (ValueError, IndexError):
                pass

        await update.message.reply_text(
            f"🧪 <b>Backtest শুরু হচ্ছে...</b>\n"
            f"📅 Period: <b>{days} দিন</b>\n"
            f"📊 Max tokens: <b>{max_tokens}</b>\n\n"
            f"⏱️ ৩০-৯০ মিনিট লাগবে।\n"
            f"শেষ হলে রিপোর্ট পাঠাবো।",
            parse_mode="HTML"
        )

        async def progress_callback(current, total):
            try:
                await update.message.reply_text(
                    f"⏳ Backtest progress: {current}/{total}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        async def run_in_bg():
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    dex = DexScreenerClient(session)
                    helius = HeliusClient(session)

                    async def bt_send(text):
                        try:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id, text=text,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logging.error(f"bt_send error: {e}")

                    engine = BacktestEngine(session, dex, helius, bt_send)
                    await engine.run(days=days, max_tokens=max_tokens, progress_callback=progress_callback)
            except Exception as e:
                logging.error(f"Backtest error: {e}", exc_info=True)
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"❌ Backtest এরর: {e}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

        asyncio.create_task(run_in_bg())

    async def cmd_lastbacktest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not os.path.exists(REPORTS_DIR):
            await update.message.reply_text("❌ কোনো backtest রিপোর্ট নেই।")
            return
        files = sorted(
            [f for f in os.listdir(REPORTS_DIR) if f.startswith("backtest_") and f.endswith(".json")],
            reverse=True
        )
        if not files:
            await update.message.reply_text("❌ কোনো backtest রিপোর্ট নেই।")
            return
        latest = os.path.join(REPORTS_DIR, files[0])
        try:
            with open(latest, "r") as f:
                data = json.load(f)
            metrics = data.get("metrics", {})
            period = data.get("period_days", 30)
            text = (
                f"📊 <b>শেষ Backtest</b>\n"
                f"📅 {files[0].replace('backtest_', '').replace('.json', '')}\n"
                f"⏱️ Period: {period} দিন\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 Total: <b>{metrics.get('total_tokens', 0)}</b>\n"
                f"🚀 Pumps: <b>{metrics.get('actual_pumps', 0)}</b>\n"
                f"🎯 Precision: <b>{metrics.get('precision', 0)}%</b>\n"
                f"📈 Recall: <b>{metrics.get('recall', 0)}%</b>\n"
                f"⚖️ F1: <b>{metrics.get('f1_score', 0)}</b>\n"
                f"✅ Accuracy: <b>{metrics.get('accuracy', 0)}%</b>\n"
                f"💰 Win Rate: <b>{metrics.get('win_rate', 0)}%</b>\n"
                f"📈 Avg Multiplier: <b>{metrics.get('avg_multiplier', 0)}x</b>"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ রিপোর্ট পড়তে এরর: {e}")

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
    app.add_handler(CommandHandler("backtest", handlers.cmd_backtest))
    app.add_handler(CommandHandler("lastbacktest", handlers.cmd_lastbacktest))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_buttons))
