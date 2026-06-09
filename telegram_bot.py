import logging
import os
import json
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from bot_state import BotState
from learner import get_stats, get_daily_report, learn_pump, learn_dump, is_duplicate, verify_pump
from dex_client import DexScreenerClient
from helius_client import HeliusClient
from config import config
from utils import format_number
from backtest import BacktestEngine, REPORTS_DIR
from signal_filter import SignalFilter
from verify_loop import VerifyLoop
from paper_trader import PaperTrader

logger = logging.getLogger("telegram_bot")

def main_keyboard():
    keyboard = [
        [KeyboardButton("📊 স্ট্যাটাস"), KeyboardButton("📈 পারফরম্যান্স")],
        [KeyboardButton("💰 ব্যালেন্স"), KeyboardButton("📦 পজিশন")],
        [KeyboardButton("✅ অন"), KeyboardButton("❌ অফ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

class TelegramHandlers:
    def __init__(self, state: BotState, dex: DexScreenerClient, session, filter_engine: SignalFilter = None, verify_loop: VerifyLoop = None, paper_trader: PaperTrader = None):
        self.state = state
        self.dex = dex
        self.session = session
        self.filter_engine = filter_engine
        self.verify_loop = verify_loop
        self.paper_trader = paper_trader

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "🤖 <b>Bappis Trade Bot v3</b>\n"
            "🌟 AI + Whale + Sentiment + Paper Trading\n\n"
            "📚 কমান্ড:\n"
            "/pump — পাম্প শেখান\n"
            "/dump — ডাম্প শেখান\n"
            "/forcepump — ফোর্স পাম্প শেখান\n"
            "/threshold — থ্রেশোল্ড সেট\n"
            "/config — কনফিগারেশন\n"
            "/backtest — backtest\n"
            "/signalstats — সিগন্যাল পরিসংখ্যান\n"
            "/retrain — model retrain"
        )
        await update.message.reply_text(
            text,
            parse_mode="HTML", reply_markup=main_keyboard()
        )

    async def cmd_threshold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        current = await self.state.get_threshold()
        filter_thr = self.filter_engine.effective_threshold() if self.filter_engine else current
        keyboard = [
            [
                InlineKeyboardButton("50%", callback_data="thr_50"),
                InlineKeyboardButton("65%", callback_data="thr_65"),
                InlineKeyboardButton("80%", callback_data="thr_80"),
                InlineKeyboardButton("90%", callback_data="thr_90"),
            ],
            [
                InlineKeyboardButton("📊 স্ট্যাটাস", callback_data="thr_status"),
            ],
        ]
        if not context.args:
            await update.message.reply_text(
                f"🎯 <b>AI কনফিডেন্স / থ্রেশোল্ড</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"বর্তমান: <b>{int(current*100)}%</b>\n"
                f"ফিল্টার থ্রেশোল্ড: <b>{int(filter_thr*100)}%</b>\n"
                f"ডিফল্ট: <b>80%</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"নিচের বাটন থেকে সিলেক্ট করো বা <code>/threshold N</code> লেখো (১-১০০)।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        try:
            val = int(context.args[0])
            if not 1 <= val <= 100:
                await update.message.reply_text("❌ ১-১০০ এর মধ্যে দিন।")
                return
            await self._apply_threshold(val / 100)
            await update.message.reply_text(
                f"✅ থ্রেশোল্ড: <b>{val}%</b>\n"
                f"ফিল্টারে প্রয়োগ হয়েছে।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except (ValueError, IndexError):
            await update.message.reply_text("❌ যেমন: /threshold 80")

    async def _apply_threshold(self, value: float) -> None:
        await self.state.set_threshold(value)
        if self.filter_engine and hasattr(self.filter_engine, "set_user_threshold"):
            self.filter_engine.set_user_threshold(value)

    async def threshold_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "thr_status":
            current = await self.state.get_threshold()
            filter_thr = self.filter_engine.effective_threshold() if self.filter_engine else current
            await query.edit_message_text(
                f"📊 <b>থ্রেশোল্ড স্ট্যাটাস</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"State threshold: <b>{int(current*100)}%</b>\n"
                f"Filter threshold: <b>{int(filter_thr*100)}%</b>\n"
                f"User override: {'<b>চালু</b>' if self.filter_engine and self.filter_engine.user_threshold is not None else 'বন্ধ'}",
                parse_mode="HTML",
                reply_markup=query.message.reply_markup,
            )
            return
        if data.startswith("thr_"):
            try:
                val = int(data.split("_", 1)[1])
            except (ValueError, IndexError):
                return
            await self._apply_threshold(val / 100)
            keyboard = [
                [
                    InlineKeyboardButton("50%", callback_data="thr_50"),
                    InlineKeyboardButton("65%", callback_data="thr_65"),
                    InlineKeyboardButton("80%", callback_data="thr_80"),
                    InlineKeyboardButton("90%", callback_data="thr_90"),
                ],
                [
                    InlineKeyboardButton("📊 স্ট্যাটাস", callback_data="thr_status"),
                ],
            ]
            await query.edit_message_text(
                f"✅ থ্রেশোল্ড সেট: <b>{val}%</b>\n"
                f"ফিল্টারে প্রয়োগ হয়েছে।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

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
        thr = self.filter_engine.effective_threshold() if self.filter_engine else 0.60
        await update.message.reply_text(
            f"📊 <b>বট স্ট্যাটাস</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"অবস্থা: {active}\n"
            f"🆕 লঞ্চ: <b>{stats['launch_tracking']}</b> | "
            f"🔍 মাইগ্রেশন: <b>{stats['tracked_coins']}</b>\n"
            f"🚫 ব্ল্যাকলিস্ট: <b>{stats['blacklisted']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📚 পাম্প প্যাটার্ন: <b>{learner_stats['pump_patterns']}</b>\n"
            f"📖 লঞ্চ প্যাটার্ন: <b>{learner_stats['launch_patterns']}</b>\n"
            f"📉 ডাম্প প্যাটার্ন: <b>{learner_stats['dump_patterns']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ সিগন্যাল পাঠানো: <b>{learner_stats['total_signals']}</b>\n"
            f"🏆 সফল (2x+): <b>{learner_stats['successful_signals']}</b>\n"
            f"🎯 একুরেসি: <b>{learner_stats['accuracy']}%</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🎯 থ্রেশোল্ড: <b>{int(thr*100)}%</b>",
            parse_mode="HTML"
        )

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from config import config
        birdeye_status = "✅" if config.birdeye_api_key else "❌"
        twitter_status = "✅" if config.twitter_bearer_token else "❌"
        await update.message.reply_text(
            f"⚙️ <b>কনফিগারেশন</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📈 পাম্প মাল্টিপ্লায়ার: <b>{config.pump_multiplier}x</b>\n"
            f"🎯 AI থ্রেশোল্ড: <b>{int(config.ai_threshold*100)}%</b>\n"
            f"💧 মিন লিকুইডিটি: <b>{format_number(config.min_liquidity)}</b>\n"
            f"💰 MCap: {format_number(config.min_mcap)} - {format_number(config.max_mcap)}\n"
            f"⏱️ স্ক্যান ইন্টারভাল: <b>{config.scan_interval}s</b>\n"
            f"🔄 প্রি-মাইগ্রেশন: <b>{'চালু' if config.enable_pre_migration else 'বন্ধ'}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🐋 Whale tracking: {birdeye_status} (min {config.whale_min_sol} SOL)\n"
            f"🐦 Twitter sentiment: {twitter_status}\n"
            f"📊 Birdeye: {birdeye_status}\n"
            f"🔄 Jupiter price: ✅",
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
                f"🌟 5x Pumps: <b>{metrics.get('actual_5x', 0)}</b>\n"
                f"🎯 Precision: <b>{metrics.get('precision', 0)}%</b>\n"
                f"📈 Recall: <b>{metrics.get('recall', 0)}%</b>\n"
                f"⚖️ F1: <b>{metrics.get('f1_score', 0)}</b>\n"
                f"✅ Accuracy: <b>{metrics.get('accuracy', 0)}%</b>\n"
                f"💰 Win Rate: <b>{metrics.get('win_rate', 0)}%</b>\n"
                f"🌟 5x Precision: <b>{metrics.get('five_x_precision', 0)}%</b>\n"
                f"📈 Avg Multiplier: <b>{metrics.get('avg_multiplier', 0)}x</b>"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ রিপোর্ট পড়তে এরর: {e}")

    async def cmd_backtest_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not os.path.exists(REPORTS_DIR):
            await update.message.reply_text("❌ কোনো backtest রিপোর্ট নেই।")
            return
        files = sorted(
            [f for f in os.listdir(REPORTS_DIR) if f.startswith("backtest_") and f.endswith(".json")]
        )
        if not files:
            await update.message.reply_text("❌ কোনো backtest রিপোর্ট নেই।")
            return
        
        trend_data = []
        for f in files[-10:]:  # Last 10 backtests
            try:
                with open(os.path.join(REPORTS_DIR, f), "r") as fp:
                    data = json.load(fp)
                metrics = data.get("metrics", {})
                trend_data.append({
                    "date": f.replace("backtest_", "").replace(".json", ""),
                    "period_days": data.get("period_days", 30),
                    "total_tokens": metrics.get("total_tokens", 0),
                    "actual_pumps": metrics.get("actual_pumps", 0),
                    "actual_5x": metrics.get("actual_5x", 0),
                    "win_rate": metrics.get("win_rate", 0),
                    "precision": metrics.get("precision", 0),
                    "recall": metrics.get("recall", 0),
                    "f1_score": metrics.get("f1_score", 0),
                    "accuracy": metrics.get("accuracy", 0),
                    "avg_multiplier": metrics.get("avg_multiplier", 0),
                    "trained_pumps": metrics.get("trained_pumps", 0),
                    "trained_early_pumps": metrics.get("trained_early_pumps", 0),
                    "golden_promoted": metrics.get("golden_promoted", 0),
                })
            except Exception:
                continue
        
        if not trend_data:
            await update.message.reply_text("❌ কোনো valid backtest data নেই।")
            return
        
        text = "📈 <b>Backtest Trend (Last 10)</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"
        
        for d in trend_data:
            date_short = d["date"][-12:]  # HHMMSS
            text += (
                f"📅 <b>{date_short}</b> ({d['period_days']}d)\n"
                f"  Tokens: {d['total_tokens']} | Pumps: {d['actual_pumps']} | 5x: {d['actual_5x']}\n"
                f"  Win Rate: {d['win_rate']}% | Prec: {d['precision']}% | Rec: {d['recall']}%\n"
                f"  F1: {d['f1_score']} | Acc: {d['accuracy']}% | Avg M: {d['avg_multiplier']}x\n"
            )
            if d.get('trained_pumps', 0) > 0 or d.get('trained_early_pumps', 0) > 0 or d.get('golden_promoted', 0) > 0:
                text += f"  📚 Trained: {d['trained_pumps']} pumps, {d['trained_early_pumps']} early, {d['golden_promoted']} golden\n"
            text += "\n"
        
        # Trend arrows
        if len(trend_data) >= 2:
            last = trend_data[-1]
            prev = trend_data[-2]
            wr_trend = "📈" if last['win_rate'] > prev['win_rate'] else "📉" if last['win_rate'] < prev['win_rate'] else "➡️"
            pr_trend = "📈" if last['precision'] > prev['precision'] else "📉" if last['precision'] < prev['precision'] else "➡️"
            text += f"📊 <b>Trend vs Previous:</b>\n"
            text += f"  Win Rate: {wr_trend} ({prev['win_rate']}% → {last['win_rate']}%)\n"
            text += f"  Precision: {pr_trend} ({prev['precision']}% → {last['precision']}%)\n"
        
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_signalstats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.verify_loop:
            await update.message.reply_text("❌ Verify loop not initialized")
            return
        v = self.verify_loop.get_stats()
        f = self.filter_engine.get_stats() if self.filter_engine else {}
        text = (
            f"📊 <b>Signal Statistics</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<b>Verification:</b>\n"
            f"⚡ Total verified: <b>{v['total_verified']}</b>\n"
            f"✅ Pumps: <b>{v['pumps']}</b>\n"
            f"🌟 Strong pumps (5x+): <b>{v['strong_pumps']}</b>\n"
            f"❌ Dumps: <b>{v['dumps']}</b>\n"
            f"💰 Win rate: <b>{v['win_rate']}%</b>\n"
            f"🌟 5x rate: <b>{v['strong_rate']}%</b>\n\n"
            f"<b>Filter:</b>\n"
            f"🌟 Golden patterns: <b>{f.get('golden_count', 0)}</b>\n"
            f"🚫 Blacklisted: <b>{f.get('blacklist_count', 0)}</b>\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_golden(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.filter_engine:
            await update.message.reply_text("❌ Filter not initialized")
            return
        goldens = self.filter_engine.golden_patterns.get("patterns", [])
        if not goldens:
            await update.message.reply_text("❌ কোনো golden pattern নেই এখনো (5+ সফল সিগন্যাল দরকার)")
            return
        text = "🌟 <b>Golden Patterns (5x+ proven):</b>\n━━━━━━━━━━━━━━━━\n"
        for i, gp in enumerate(goldens[:10], 1):
            text += (
                f"{i}. <b>{gp.get('symbol', '?')}</b>\n"
                f"   Count: {gp.get('count', 0)} | "
                f"Max: {gp.get('max_multiplier', 0)}x | "
                f"Avg: {gp.get('avg_multiplier', 0)}x\n"
            )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_feature(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 <b>ফিচার রিকোয়েস্ট:</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "Usage: /feature ফিচারের বিবরণ\n\n"
                "Example:\n"
                "/feature whale wallet ট্র্যাক করার সিস্টেম যোগ করো\n"
                "/feature signal accuracy ৯০% এর উপরে আনো"
            )
            return
        request_text = " ".join(context.args)
        import json as _json
        feature_file = "/tmp/feature_request.txt"
        with open(feature_file, "w") as f:
            _json.dump({"request": request_text, "user": update.effective_user.id}, f)
        await update.message.reply_text(
            f"📝 <b>ফিচার রিকোয়েস্ট সেভ হয়েছে!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💬 {request_text[:200]}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏳ স্ট্যাটাস: pending\n"
            f"🤖 AI assistant এটি implement করবে।"
        )
        logger.info(f"📝 Feature request: {request_text[:100]}")

    async def cmd_blacklist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.filter_engine:
            await update.message.reply_text("❌ Filter not initialized")
            return
        count = len(self.filter_engine.blacklist.get("patterns", []))
        await update.message.reply_text(
            f"🚫 <b>Blacklisted Patterns:</b> {count} টি\n"
            f"3+ বার ব্যর্থ হলে auto-blacklist হয়।"
        )

    async def cmd_retrain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔄 Model retraining triggered...")
        from learner import _update_model, load_data
        data = load_data()
        _update_model(data)
        from learner import save_data
        save_data(data)
        learner_stats = get_stats()
        await update.message.reply_text(
            f"✅ Model updated!\n"
            f"🧠 Patterns: {learner_stats['pump_patterns']}\n"
            f"🎯 Accuracy: {learner_stats['accuracy']}%"
        )

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.paper_trader:
            await update.message.reply_text("❌ Paper Trading চালু নেই।")
            return
        await update.message.reply_text(
            self.paper_trader.format_balance(),
            parse_mode="HTML"
        )

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.paper_trader:
            await update.message.reply_text("❌ Paper Trading চালু নেই।")
            return
        await update.message.reply_text(
            self.paper_trader.format_positions(),
            parse_mode="HTML"
        )

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.paper_trader:
            await update.message.reply_text("❌ Paper Trading চালু নেই।")
            return
        limit = 10
        if context.args:
            try:
                limit = int(context.args[0])
                limit = max(1, min(50, limit))
            except (ValueError, IndexError):
                pass
        await update.message.reply_text(
            self.paper_trader.format_trade_history(limit),
            parse_mode="HTML"
        )

    async def handle_buttons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        if text == "📊 স্ট্যাটাস":
            await self.cmd_health(update, context)
        elif text == "📈 পারফরম্যান্স":
            learner_stats = get_stats()
            v = self.verify_loop.get_stats() if self.verify_loop else {}
            await update.message.reply_text(
                f"📈 <b>পারফরম্যান্স</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚡ মোট সিগন্যাল: <b>{learner_stats['total_signals']}</b>\n"
                f"✅ চেক হয়েছে: <b>{v.get('total_verified', 0)}</b>\n"
                f"🏆 সফল (2x+): <b>{v.get('pumps', 0)}</b>\n"
                f"🌟 স্ট্রং (5x+): <b>{v.get('strong_pumps', 0)}</b>\n"
                f"❌ ডাম্প: <b>{v.get('dumps', 0)}</b>\n"
                f"🎯 একুরেসি: <b>{v.get('win_rate', 0)}%</b>\n"
                f"⏰ সেরা সময়: <b>{learner_stats['best_hour']}:00 UTC</b>",
                parse_mode="HTML"
            )
        elif text == "💰 ব্যালেন্স":
            await self.cmd_balance(update, context)
        elif text == "📦 পজিশন":
            await self.cmd_positions(update, context)
        elif text == "✅ অন":
            await self.state.set_bot_active(True)
            await update.message.reply_text("✅ বট চালু!")
        elif text == "❌ অফ":
            await self.state.set_bot_active(False)
            await update.message.reply_text("❌ বট বন্ধ!")


def register_handlers(app, handlers: TelegramHandlers):
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("pump", handlers.cmd_pump))
    app.add_handler(CommandHandler("dump", handlers.cmd_dump))
    app.add_handler(CommandHandler("forcepump", handlers.cmd_forcepump))
    app.add_handler(CommandHandler("threshold", handlers.cmd_threshold))
    app.add_handler(CommandHandler("health", handlers.cmd_health))
    app.add_handler(CommandHandler("config", handlers.cmd_config))
    app.add_handler(CommandHandler("backtest", handlers.cmd_backtest))
    app.add_handler(CommandHandler("lastbacktest", handlers.cmd_lastbacktest))
    app.add_handler(CommandHandler("backtesttrend", handlers.cmd_backtest_trend))
    app.add_handler(CommandHandler("signalstats", handlers.cmd_signalstats))
    app.add_handler(CommandHandler("golden", handlers.cmd_golden))
    app.add_handler(CommandHandler("blacklist", handlers.cmd_blacklist))
    app.add_handler(CommandHandler("retrain", handlers.cmd_retrain))
    app.add_handler(CommandHandler("balance", handlers.cmd_balance))
    app.add_handler(CommandHandler("positions", handlers.cmd_positions))
    app.add_handler(CommandHandler("trades", handlers.cmd_trades))
    app.add_handler(CommandHandler("feature", handlers.cmd_feature))
    app.add_handler(CallbackQueryHandler(handlers.threshold_callback, pattern="^thr_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_buttons))
