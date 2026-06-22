import logging
import os
import json
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from bot_state import BotState
from learner import get_stats, get_daily_report, is_duplicate, get_performance_report, enhanced_auto_learn
from dex_client import DexScreenerClient
from helius_client import HeliusClient
from config import config
from utils import format_number
from backtest import BacktestEngine, REPORTS_DIR
from paper_trader import PaperTrader

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chat_id")
CHANNEL_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".channel_id")

logger = logging.getLogger("telegram_bot")

def _save_channel_id(channel_id: str):
    try:
        with open(CHANNEL_ID_FILE, "w") as f:
            f.write(str(channel_id))
        config.channel_id = str(channel_id)
        logger.info(f"✅ Channel ID saved: {channel_id}")
    except Exception:
        pass

def main_keyboard():
    keyboard = [
        [KeyboardButton("📊 স্ট্যাটাস"), KeyboardButton("📈 পারফরম্যান্স")],
        [KeyboardButton("🔍 অ্যানালিটিক্স"), KeyboardButton("⚙️ কনফিগ")],
        [KeyboardButton("✅ অন"), KeyboardButton("❌ অফ")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def _save_chat_id(chat_id: str):
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception:
        pass

class TelegramHandlers:
    def __init__(self, state: BotState, dex: DexScreenerClient, session, paper_trader: PaperTrader = None):
        self.state = state
        self.dex = dex
        self.session = session
        self.paper_trader = paper_trader

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        _save_chat_id(update.effective_chat.id)
        text = (
            "🤖 <b>Meme Coin Auto-Trade Bot</b>\n"
            "🌟 AI লার্নিং + পেপার ট্রেডিং\n\n"
            "📚 কমান্ড:\n"
            "/pump — পাম্প শেখান\n"
            "/dump — ডাম্প শেখান\n"
            "/forcepump — ফোর্স পাম্প শেখান\n"
            "/threshold — থ্রেশোল্ড সেট\n"
            "/config — কনফিগারেশন\n"
            "/balance — ব্যালেন্স দেখুন\n"
            "/positions — ওপেন পজিশন দেখুন\n"
            "/trades — ট্রেড হিস্ট্রি\n"
            "/signalstats — সিগন্যাল পরিসংখ্যান\n"
            "/freshstats — ফ্রেশ পারফরম্যান্স\n"
            "/retrain — model retrain\n"
            "/autolearn — স্মার্ট অটো-লার্নিং"
        )
        await update.message.reply_text(
            text,
            parse_mode="HTML", reply_markup=main_keyboard()
        )

    async def cmd_threshold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        current = await self.state.get_threshold()
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
                f"✅ থ্রেশোল্ড: <b>{val}%</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except (ValueError, IndexError):
            await update.message.reply_text("❌ যেমন: /threshold 80")

    async def _apply_threshold(self, value: float) -> None:
        await self.state.set_threshold(value)

    async def threshold_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "thr_status":
            current = await self.state.get_threshold()
            await query.edit_message_text(
                f"📊 <b>থ্রেশোল্ড স্ট্যাটাস</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"State threshold: <b>{int(current*100)}%</b>",
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
        from utils import verify_pump
        verified, actual_multi = verify_pump(pair, config.pump_multiplier)
        if not verified:
            await update.message.reply_text(
                f"⚠️ {config.pump_multiplier}x ভেরিফাই হয়নি ({actual_multi}x)\n/forcepump {address}",
                parse_mode="HTML"
            )
            return
        from learner import record_signal_result
        record_signal_result(address, symbol, actual_multi)
        await update.message.reply_text(f"✅ <b>{name}</b>\nPump learned: {actual_multi}x", parse_mode="HTML")

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
        from learner import record_signal_result
        from config import config as cfg
        record_signal_result(address, symbol, cfg.pump_multiplier)
        await update.message.reply_text(f"✅ <b>{name}</b>\nForce pump learned: {cfg.pump_multiplier}x", parse_mode="HTML")

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
        from learner import record_signal_result
        record_signal_result(address, symbol, 0.5)
        await update.message.reply_text(f"✅ <b>{name}</b>\nDump learned (0.5x)", parse_mode="HTML")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = await self.state.get_stats()
        learner_stats = get_stats()
        active = "🟢 চালু" if stats["bot_active"] else "🔴 বন্ধ"

        # Get auto-fix and hourly stats
        try:
            from learner import get_bad_hours, load_data as _ld
            _d = _ld()
            auto_fix = _d.get("model", {}).get("auto_fix_history", [])
            hourly = _d.get("model", {}).get("hourly_stats", {})
            bad_hours = get_bad_hours(min_signals=3, max_win_rate=0.15)
            recent_fixes = auto_fix[-5:]
            fix_count = len(auto_fix)
        except Exception:
            bad_hours = set()
            recent_fixes = []
            fix_count = 0
            hourly = {}

        # Build hourly line
        if hourly:
            hour_parts = []
            for h in sorted(hourly.keys(), key=lambda x: int(x)):
                s = hourly[h]
                t = s.get("total", 0)
                w = s.get("wins", 0)
                wr = (w / t * 100) if t > 0 else 0
                emoji = "🟢" if wr > 25 else ("🟡" if wr > 15 else "🔴")
                hour_parts.append(f"{emoji}{int(h):02d}")
            hour_line = " ".join(hour_parts)
        else:
            hour_line = "No data yet"

        text = (
            f"📊 <b>বট স্ট্যাটাস</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"অবস্থা: {active}\n"
            f"🆕 লঞ্চ: <b>{stats['launch_tracking']}</b> | "
            f"🔍 মাইগ্রেশন: <b>{stats['tracked_coins']}</b>\n"
            f"🚫 ব্ল্যাকলিস্ট: <b>{stats['blacklisted']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📚 পাম্প প্যাটার্ন: <b>{learner_stats['pump_patterns']}</b>\n"
            f"📉 ডাম্প প্যাটার্ন: <b>{learner_stats['dump_patterns']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ সিগন্যাল: <b>{learner_stats['total_signals']}</b>\n"
            f"🏆 সফল: <b>{learner_stats['successful_signals']}</b>\n"
            f"🎯 একুরেসি: <b>{learner_stats['accuracy']}%</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>ঘণ্টা ভিত্তিক:</b> {hour_line}\n"
        )
        if bad_hours:
            text += f"🚫 <b>ব্যাড আওয়ার:</b> {', '.join(f'{h}:00' for h in sorted(bad_hours))}\n"
        text += f"🔧 <b>অটো-ফিক্স:</b> {fix_count} fixes applied"
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_patterns(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show exact patterns bot uses for signal decisions."""
        data = load_data()
        pumps = data.get("pump_patterns", [])
        dumps = data.get("dump_patterns", [])
        criteria = data.get("model", {}).get("signal_criteria", {})

        # Top pump patterns
        top_pumps = sorted(pumps, key=lambda x: x.get("ath_multiplier", 0), reverse=True)[:8]
        pump_lines = ""
        for p in top_pumps:
            feat = p
            if "features" in p and "buy_sell_ratio" not in p:
                feat = p["features"]
            sym = p.get("symbol", "?")[:10]
            bsr = feat.get("buy_sell_ratio", 0)
            buys = feat.get("buy_count", 0)
            liq = feat.get("initial_liq", 0)
            ath = p.get("ath_multiplier", 0)
            pump_lines += f"  • {sym}: BSR={bsr:.1f} buys={buys} liq=${liq:.0f} → {ath:.1f}x\n"

        # Top dump patterns
        top_dumps = sorted(dumps, key=lambda x: x.get("features", x).get("holders", 0), reverse=True)[:5]
        dump_lines = ""
        for d in top_dumps:
            feat = d.get("features", d)
            sym = d.get("symbol", "?")[:10]
            bsr = feat.get("buy_sell_ratio", 0)
            holders = feat.get("holders", 0)
            dump_lines += f"  • {sym}: BSR={bsr:.1f} holders={holders}\n"

        text = (
            f"🎯 <b>সিগন্যাল প্যাটার্ন</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 <b>ফিল্টার:</b>\n"
            f"  • BSR ≥ {criteria.get('min_bsr', 'N/A')}\n"
            f"  • Holders ≥ {criteria.get('min_holders', 'N/A')}\n"
            f"  • Wallets ≥ {criteria.get('min_wallets', 'N/A')}\n"
            f"  • Pattern ≥ {criteria.get('pattern_threshold', 'N/A')}\n"
            f"  • Heuristic ≥ {criteria.get('heuristic_threshold', 'N/A')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🟢 <b>পাম্প প্যাটার্ন ({len(pumps)}টি):</b>\n"
            f"{pump_lines}"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔴 <b>ডাম্প প্যাটার্ন ({len(dumps)}টি):</b>\n"
            f"{dump_lines}"
            f"━━━━━━━━━━━━━━━━\n"
            f"💡 <b>নিয়ম:</b>\n"
            f"  • পাম্প প্যাটার্ন ম্যাচ → সিগনাল\n"
            f"  • ডাম্প প্যাটার্ন ম্যাচ → রিজেক্ট\n"
            f"  • হিউরিস্টিক স্কোর → ব্যাকআপ"
        )
        await update.message.reply_text(text, parse_mode="HTML")

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
        learner_stats = get_stats()
        text = (
            f"📊 <b>Signal Statistics</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ Total signals: <b>{learner_stats['total_signals']}</b>\n"
            f"🏆 Successful (2x+): <b>{learner_stats['successful_signals']}</b>\n"
            f"🎯 Accuracy: <b>{learner_stats['accuracy']}%</b>\n"
            f"📚 Pump patterns: <b>{learner_stats['pump_patterns']}</b>\n"
            f"📉 Dump patterns: <b>{learner_stats['dump_patterns']}</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_perf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Scraper-ready TP/SL report — 1 message."""
        perf = get_performance_report()

        total = perf["total"]
        total_all = perf.get("total_all_data", 0)
        tp_h = perf.get("tp_hits", 0)
        sl_h = perf.get("sl_hits", 0)

        if total == 0 and total_all == 0:
            await update.message.reply_text(
                "📊 <b>পারফরম্যান্স</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "গত ২৪ ঘন্টায় কোনো সিগন্যাল নেই।",
                parse_mode="HTML"
            )
            return

        scenarios = perf.get("tp_scenarios", [])
        hit_scenarios = [s for s in scenarios if s['tp_hits'] > 0]
        opt_scenario = [s for s in scenarios if s['tp'] == perf['optimal_tp']]
        show_scenarios = hit_scenarios[:6]
        if opt_scenario and opt_scenario[0] not in show_scenarios:
            show_scenarios.append(opt_scenario[0])

        sc_lines = ""
        for sc in show_scenarios:
            star = "⭐" if sc['tp'] == perf['optimal_tp'] else "  "
            sc_lines += (
                f"  {star} +{sc['tp']:>3}%: "
                f"{sc['tp_hits']}/{total} ({sc['tp_rate']:.0f}%) "
                f"= <b>{sc['avg_pnl']:+.0f}%</b>\n"
            )

        text = (
            f"📊 <b>পারফরম্যান্স</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⭐ <b>সেট করো:</b> TP +{perf['optimal_tp']}% / SL {perf['optimal_sl']}%\n"
            f"  → গড় লাভ: <b>{perf['expected_pnl']:+.1f}%</b>\n"
            f"  → জিতবে {tp_h}/{tp_h + sl_h} | হারবে {sl_h}/{tp_h + sl_h}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 <b>টিপি/এসএল:</b>\n"
            f"{sc_lines}"
            f"━━━━━━━━━━━━━━━━\n"
        )

        # Add hourly stats
        try:
            from learner import get_hourly_stats_report, get_bad_hours
            hourly_report = get_hourly_stats_report()
            bad_hours = get_bad_hours(min_signals=3, max_win_rate=0.15)
            text += f"{hourly_report}\n"
            if bad_hours:
                text += f"🚫 <b>ব্যাড আওয়ার:</b> {', '.join(f'{h}:00' for h in sorted(bad_hours))}\n"
            else:
                text += "✅ <b>সব আওয়ার OK</b>\n"
        except Exception:
            pass

        if total == 0 and total_all > 0:
            text += f"📊 <i>{total_all} টি ঐতিহাসিক সিগন্যাল থেকে গণনা</i>\n"
        text += f"🕐 <i>গত ২৪ ঘন্টা</i>"
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_golden(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Golden patterns removed in v4. Use /signalstats instead.")

    async def cmd_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comprehensive analytics with time patterns, launch patterns, success factors."""
        from learner import get_comprehensive_analytics
        report = get_comprehensive_analytics()
        await update.message.reply_text(report, parse_mode="HTML")

    async def cmd_blacklist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Blacklist removed in v4. Honeypot detection is automatic.")

    async def handle_channel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Auto-detect channel ID when bot receives channel post."""
        channel = update.effective_chat
        if channel and channel.type == "channel":
            channel_id = str(channel.id)
            if not config.channel_id or config.channel_id != channel_id:
                _save_channel_id(channel_id)
                logger.info(f"📢 Channel detected: {channel.title} ({channel_id})")

    async def cmd_setchannel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set channel ID manually: /setchannel -100xxxxxxxxxx"""
        if not context.args:
            # No args = clear channel ID
            config.channel_id = ""
            try:
                os.remove(CHANNEL_ID_FILE)
            except Exception:
                pass
            await update.message.reply_text(
                "📢 <b>Channel cleared!</b>\n"
                "Channel ID মুছে ফেলা হয়েছে।",
                parse_mode="HTML"
            )
            return
        channel_id = context.args[0]
        # Validate numeric ID
        if not channel_id.lstrip('-').isdigit() or not channel_id.startswith('-100'):
            await update.message.reply_text(
                "❌ <b>ভুল Channel ID!</b>\n"
                "Format: /setchannel -100xxxxxxxxxx\n"
                "Channel ID অবশ্যই -100 দিয়ে শুরু হতে হবে।",
                parse_mode="HTML"
            )
            return
        _save_channel_id(channel_id)
        await update.message.reply_text(
            f"✅ Channel ID set: <code>{channel_id}</code>",
            parse_mode="HTML"
        )

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

    async def cmd_retrain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔄 Retraining...")
        learner_stats = get_stats()
        await update.message.reply_text(
            f"✅ Current stats:\n"
            f"📚 Patterns: {learner_stats['pump_patterns']}\n"
            f"🎯 Accuracy: {learner_stats['accuracy']}%"
        )

    async def cmd_autolearn(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger enhanced auto-learn to update signal thresholds based on current performance."""
        await update.message.reply_text("🧠 Enhanced auto-learn triggering...")
        
        try:
            result = enhanced_auto_learn()
            if result:
                await update.message.reply_text(
                    f"✅ Enhanced auto-learn completed!\n"
                    f"📊 Quality Score: {result['quality_score']}%\n"
                    f"🎯 New Threshold: {result['heuristic_threshold']}\n"
                    f"📈 Pattern Threshold: {result['pattern_threshold']}\n"
                    f"🎲 Volatility Setting: {result['volatility_setting']}\n"
                    f"📈 Win Rate: {result['metrics']['win_rate']*100:.1f}%\n"
                    f"💰 Average PnL: {result['metrics']['avg_pnl']:+.1f}%\n"
                    f"📊 Pattern Strength: {result['metrics']['pattern_strength']:.2f}\n"
                    f"📊 Dump Rate: {result['metrics']['dump_rate']*100:.1f}%\n"
                    f"🎲 Average ATH: {result['metrics']['avg_ath']:.2f}x\n"
                )
            else:
                await update.message.reply_text("❌ Enhanced auto-learn failed (insufficient data)")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def cmd_dailybest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show today's best coins from all 5 channels."""
        from telegram_collector import find_daily_best
        best = find_daily_best()
        if not best or best["total_analyzed"] == 0:
            await update.message.reply_text("📊 এখনো পর্যাপ্ত ডেটা নেই।", parse_mode="HTML")
            return

        top10 = best["top10"][:5]
        lines = f"📊 <b>আজকের সেরা কয়েন (৫ চ্যানেল)</b>\n━━━━━━━━━━━━━━━━\n"
        for i, t in enumerate(top10, 1):
            ath = t.get("ath_multiplier", 0)
            emoji = "🔥" if ath >= 50 else "✅" if ath >= 5 else "📈"
            ch = t.get("source_channel_name", "?")
            sig = t.get("signal_type", "?")
            lines += f"{emoji} #{i} <b>${t.get('symbol', '?')}</b> x{ath:.1f} — {ch} [{sig}]\n"

        combos = best.get("best_combos", [])[:3]
        if combos:
            lines += f"\n🎯 <b>সেরা প্যাটার্ন:</b>\n"
            for c in combos:
                lines += f"  • {c['pattern']}: <b>{c['win_rate']}%</b> win ({c['winners']}/{c['total']})\n"

        lines += f"\n📊 <i>{best['total_analyzed']} টি কয়েন বিশ্লেষিত</i>"
        await update.message.reply_text(lines, parse_mode="HTML")

    async def cmd_patterns(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show full pattern analysis from all channels."""
        from telegram_collector import learn_channel_patterns
        insights = learn_channel_patterns()

        text = "🎯 <b>কয়েন প্যাটার্ন বিশ্লেষণ</b>\n━━━━━━━━━━━━━━━━\n"

        text += "\n👥 <b>হোল্ডার:</b>\n"
        for rng, data in insights.get("holder_ranges", {}).items():
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            emoji = "🟢" if wr > 40 else "🟡" if wr > 25 else "🔴"
            if n > 0:
                text += f"  {emoji} {rng}: {n}টি | 🏆 {wr:.0f}% জয়\n"

        text += "\n💰 <b>ডেভ ব্যালেন্স (SOL):</b>\n"
        for rng, data in insights.get("dev_ranges", {}).items():
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            emoji = "🟢" if wr > 40 else "🟡" if wr > 25 else "🔴"
            if n > 0:
                text += f"  {emoji} {rng}: {n}টি | 🏆 {wr:.0f}% জয়\n"

        text += "\n💧 <b>লিকুইডিটি:</b>\n"
        for rng, data in insights.get("liq_ranges", {}).items():
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            emoji = "🟢" if wr > 40 else "🟡" if wr > 25 else "🔴"
            if n > 0:
                text += f"  {emoji} ${rng}: {n}টি | 🏆 {wr:.0f}% জয়\n"

        text += "\n📡 <b>সিগনাল টাইপ:</b>\n"
        for sig, data in insights.get("signal_types", {}).items():
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            emoji = "🟢" if wr > 40 else "🟡" if wr > 25 else "🔴"
            if n > 0:
                text += f"  {emoji} {sig}: {n}টি | 🏆 {wr:.0f}% জয়\n"

        text += "\n📺 <b>চ্যানেল তুলনা:</b>\n"
        for ch, data in insights.get("channels", {}).items():
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            emoji = "🟢" if wr > 40 else "🟡" if wr > 25 else "🔴"
            if n > 0:
                text += f"  {emoji} {ch}: {n}টি | 🏆 {wr:.0f}% জয়\n"

        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_similar(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if a token matches known winning patterns."""
        if not context.args:
            await update.message.reply_text("❌ /similar TOKEN_SYMBOL বা /similar CONTRACT_ADDRESS")
            return

        query = context.args[0].strip()
        from telegram_collector import get_tracked_tokens
        from gmgn_scorer import check_similarity_to_patterns
        tokens = get_tracked_tokens()

        matched_token = None
        for ca, t in tokens.items():
            if t.get("symbol", "").upper() == query.upper() or ca == query:
                matched_token = t
                break

        if not matched_token:
            await update.message.reply_text(f"❌ '{query}' ট্র্যাকে নেই।")
            return

        result = check_similarity_to_patterns(matched_token)
        t = matched_token

        text = (
            f"🔍 <b>সিমিলারিটি চেক: ${t.get('symbol', '?')}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📡 সিগনাল: <b>{t.get('signal_type', '?')}</b>\n"
            f"👥 হোল্ডার: <b>{t.get('holders', 0)}</b>\n"
            f"💰 ডেভ: <b>{t.get('dev_balance_sol', 0):.2f} SOL</b>\n"
            f"💧 লিক: <b>${t.get('liq_usd', 0):,.0f}</b>\n"
            f"📈 ATH: <b>x{t.get('ath_multiplier', 1):.1f}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🎯 ম্যাচ স্কোর: <b>{result['similarity']:.0%}</b>\n"
            f"🏅 কনফিডেন্স: <b>{result['confidence']}</b>\n"
        )
        if result["matched_patterns"]:
            text += f"\n✅ <b>ম্যাচড প্যাটার্ন:</b>\n"
            for p in result["matched_patterns"]:
                text += f"  • {p}\n"

        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_channelstats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show per-channel performance comparison."""
        from telegram_collector import find_daily_best
        best = find_daily_best()
        patterns = best.get("patterns", {})
        ch_data = patterns.get("channel", {})

        if not ch_data:
            await update.message.reply_text("📊 এখনো পর্যাপ্ত ডেটা নেই।", parse_mode="HTML")
            return

        text = "📺 <b>চ্যানেল পারফরম্যান্স</b>\n━━━━━━━━━━━━━━━━\n"
        sorted_ch = sorted(ch_data.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True)
        for ch, data in sorted_ch:
            wr = data.get("win_rate", 0)
            n = data.get("total", 0)
            w = data.get("winners", 0)
            emoji = "🥇" if wr == max(d.get("win_rate", 0) for _, d in sorted_ch) else "🥈" if wr > 30 else "🥉"
            text += f"{emoji} <b>{ch}</b>\n"
            text += f"  📊 {n}টি কয়েন | 🏆 {w}টি জয় | <b>{wr:.1f}%</b>\n"

        text += f"\n📊 <i>মোট {best.get('total_analyzed', 0)} টি কয়েন বিশ্লেষিত</i>"
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_freshstats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show fresh performance stats from the current starting point."""
        from learner import calculate_signal_review, load_data
        data = load_data()
        fresh_start = data.get("fresh_start", "")

        if not fresh_start:
            await update.message.reply_text("❌ Fresh start marker not found.", parse_mode="HTML")
            return

        review = calculate_signal_review()
        signals = review.get("signals", [])
        total = review.get("total", 0)
        wins = review.get("wins", 0)
        losses = review.get("losses", 0)
        win_rate = review.get("win_rate", 0)
        avg_ath = review.get("avg_ath", 0)
        best = review.get("best")
        worst = review.get("worst")

        if not signals:
            await update.message.reply_text(
                f"📊 <b>Fresh Performance</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🕐 Since: {fresh_start[:16]}\n"
                f"📈 No signals yet since fresh start.",
                parse_mode="HTML"
            )
            return

        text = f"📊 <b>FRESH PERFORMANCE</b>\n"
        text += f"━━━━━━━━━━━━━━━━\n"
        text += f"🕐 Since: {fresh_start[:16]}\n"
        text += f"📈 Total: {total} | Win: {wins} | Loss: {losses}\n"
        text += f"🎯 Win Rate: <b>{win_rate}%</b>\n"
        text += f"📈 Avg ATH: <b>{avg_ath}x</b>\n\n"

        if best:
            text += f"🏆 <b>Best:</b> {best['symbol']} +{best['actual_pump_pct']}% (ATH {best['ath_multiplier']}x)\n"
        if worst:
            text += f"💀 <b>Worst:</b> {worst['symbol']} {worst['actual_pump_pct']}% (ATH {worst['ath_multiplier']}x)\n"
        text += f"\n<b>📋 Signal Details:</b>\n"
        for s in signals[:15]:
            sym = s["symbol"]
            ath = s["ath_multiplier"]
            pump = s["actual_pump_pct"]
            sl = s["optimal_sl_pct"]
            emoji = s["status_emoji"]
            curr_str = f"now {s['current_pump_pct']}%" if s.get("current_multiplier") else "ended"
            text += f"  {emoji} <b>{sym}</b>: +{pump}% | SL: {sl}% | {curr_str}\n"

        text += f"\n━━━━━━━━━━━━━━━━\n"
        text += f"💡 TP/SL based on actual price paths"
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_convergence(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show convergence report — multi-source signal scoring."""
        try:
            from convergence_scorer import ConvergenceScorer
            scorer = ConvergenceScorer()
            report = scorer.get_report()
            await update.message.reply_text(report, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}", parse_mode="HTML")

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
        chat = update.effective_chat
        # Auto-detect channel ID
        if chat and chat.type == "channel":
            channel_id = str(chat.id)
            if not config.channel_id or config.channel_id != channel_id:
                _save_channel_id(channel_id)
                logger.info(f"📢 Channel auto-detected: {chat.title} ({channel_id})")
            return
        _save_chat_id(chat.id)
        text = update.message.text
        logger.info(f"Button pressed: {repr(text)}")
        if text == "📊 স্ট্যাটাস":
            await self.cmd_health(update, context)
        elif text == "📈 পারফরম্যান্স":
            await self.cmd_perf(update, context)
        elif text == "🔍 অ্যানালিটিক্স":
            await self.cmd_analytics(update, context)
        elif text == "⚙️ কনফিগ":
            await self.cmd_config(update, context)
        elif text == "✅ অন":
            await self.state.set_bot_active(True)
            await update.message.reply_text("✅ বট চালু!")
        elif text == "❌ অফ":
            await self.state.set_bot_active(False)
            await update.message.reply_text("❌ বট বন্ধ!")


def register_handlers(app, handlers: TelegramHandlers):
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("health", handlers.cmd_health))
    app.add_handler(CommandHandler("perf", handlers.cmd_perf))
    app.add_handler(CommandHandler("analytics", handlers.cmd_analytics))
    app.add_handler(CommandHandler("signalstats", handlers.cmd_signalstats))
    app.add_handler(CommandHandler("autolearn", handlers.cmd_autolearn))
    app.add_handler(CommandHandler("patterns", handlers.cmd_patterns))
    app.add_handler(CommandHandler("dailybest", handlers.cmd_dailybest))
    app.add_handler(CommandHandler("similar", handlers.cmd_similar))
    app.add_handler(CommandHandler("channelstats", handlers.cmd_channelstats))
    app.add_handler(CommandHandler("freshstats", handlers.cmd_freshstats))
    app.add_handler(CommandHandler("convergence", handlers.cmd_convergence))
    app.add_handler(CommandHandler("config", handlers.cmd_config))
    app.add_handler(CommandHandler("setchannel", handlers.cmd_setchannel))
    app.add_handler(CallbackQueryHandler(handlers.threshold_callback, pattern="^thr_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handlers.handle_buttons))
