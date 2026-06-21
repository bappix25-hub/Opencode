import asyncio
import aiohttp
import logging
import signal
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import config
from utils import setup_logging, format_usd, format_sol, format_pct
from wallet_manager import WalletManager
from state import TradeState, TradePosition
from market_monitor import MarketMonitor
from pump_detector import PumpDetector
from risk_manager import RiskManager
from trade_executor import TradeExecutor
from telegram_notifier import TelegramNotifier, get_main_keyboard, get_gmgn_link, get_dexscreener_link
from learner import Learner
from health import HealthChecker
from pre_migration import PreMigrationDetector
from signal_tracker import signal_tracker
from price_history import price_history
from backtest_engine import backtest_engine
from pattern_analyzer import pattern_analyzer
from weight_optimizer import weight_optimizer
from wallet_tracker import wallet_tracker
from social_sentiment import social_sentiment_engine
from chain_reaction import chain_reaction_analyzer
from market_regime import simple_regime_detector
from technical_indicators import technical_indicator_engine
from neural_engine import neural_engine
from ensemble_learner import ensemble_learner
from advanced_trading import advanced_trading
from mcap100k_predictor import mcap100k_predictor

logger = setup_logging("trading_bot")

bot_active = True
shutdown_event = asyncio.Event()


def handle_signal(sig, frame):
    global bot_active
    if sig == signal.SIGINT:
        logger.info(f"Signal {sig} received, shutting down...")
        shutdown_event.set()
    elif sig == signal.SIGTERM:
        logger.debug(f"SIGTERM ignored (PID {__import__('os').getpid()})")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


class TradingBot:
    def __init__(self):
        self.session = None
        self.wallet = WalletManager()
        self.state = TradeState()
        self.notifier = TelegramNotifier()
        self.learner = Learner()
        self.health = HealthChecker()
        self.risk = RiskManager(self.learner)
        self.monitor = None
        self.detector = None
        self.pre_detector = None
        self.executor = None
        self.app = None
        self._bot_instance = None
        self._last_status_sent = 0
        self._status_interval = 1800

    async def start(self):
        logger.info("=" * 50)
        logger.info("Trading Bot Starting (Pre-Migration + Auto-Learning)...")
        logger.info(f"Paper Mode: {config.paper_trading}")
        logger.info(f"Wallet: {self.wallet.get_public_key()}")
        logger.info(f"Learning Data: {self.learner.model_stats['total_outcomes']} outcomes")
        logger.info("=" * 50)

        async with aiohttp.ClientSession() as session:
            self.session = session
            self.monitor = MarketMonitor(session)
            self.detector = PumpDetector(self.monitor)
            self.pre_detector = PreMigrationDetector(session)
            self.executor = TradeExecutor(session, self.wallet)

            if not config.paper_trading and not self.wallet.is_loaded():
                logger.warning("Real trading requires SOLANA_PRIVATE_KEY! Running in signal-only mode.")
                config.paper_trading = True

            sol_balance = 0.0
            if self.wallet.is_loaded():
                sol_balance = await self.wallet.get_sol_balance(session)
                logger.info(f"SOL Balance: {sol_balance:.4f}")

            try:
                self.app = Application.builder().token(config.telegram_bot_token).build()
                self._setup_handlers()

                await self.app.initialize()
                self._bot_instance = self.app.bot
                await self.app.start()
                await self.app.updater.start_polling(drop_pending_updates=True)
                logger.info("Telegram bot polling started")

                await self.notifier.notify_start(config.paper_trading)

                await asyncio.gather(
                    self.monitor_loop(),
                    self.pre_migration_loop(),
                    self.trade_loop(),
                    self.health_loop(),
                    self.learning_loop(),
                    self.signal_monitor_loop(),
                    self.lifetime_pump_loop(),
                    self.price_history_loop(),
                    self.backtest_loop(),
                    self.pattern_analysis_loop(),
                    self.weight_optimization_loop(),
                    self.wallet_tracking_loop(),
                    self.social_sentiment_loop(),
                    self.chain_reaction_loop(),
                    self.market_regime_loop(),
                    self.technical_indicators_loop(),
                    self.neural_engine_loop(),
                    self.ensemble_loop(),
                    self.mcap100k_tracking_loop(),
                    self._wait_shutdown(),
                )

            except Exception as e:
                logger.error(f"Startup error: {e}")
            finally:
                try:
                    if self.app:
                        await self.app.updater.stop()
                        await self.app.stop()
                        await self.app.shutdown()
                except Exception:
                    pass
                logger.info("Bot shutdown complete")

    async def _wait_shutdown(self):
        await shutdown_event.wait()

    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("trades", self._cmd_trades))
        self.app.add_handler(CommandHandler("signals", self._cmd_signals))
        self.app.add_handler(CommandHandler("balance", self._cmd_balance))
        self.app.add_handler(CommandHandler("position", self._cmd_position))
        self.app.add_handler(CommandHandler("config", self._cmd_config))
        self.app.add_handler(CommandHandler("restart", self._cmd_restart))
        self.app.add_handler(CommandHandler("learn", self._cmd_learn))
        self.app.add_handler(CommandHandler("accuracy", self._cmd_accuracy))
        self.app.add_handler(CommandHandler("health", self._cmd_health))
        self.app.add_handler(CommandHandler("debug", self._cmd_debug))
        self.app.add_handler(CommandHandler("mc100k", self._cmd_mc100k))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        global bot_active
        bot_active = True
        await update.message.reply_text(
            "🟢 <b>Bot Started!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Mode: <b>SIGNAL ONLY</b>\n"
            "Monitoring: Pre-migration + Post-migration\n"
            "Auto-learning active.\n\n"
            "📌 <b>Commands:</b>\n"
            "📊 Status - অবস্থা\n"
            "🚨 Signals - সিগনাল রিপোর্ট\n"
            "⚙️ Config - সেটিংস\n"
            "🔄 Restart - রিস্টার্ট",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        global bot_active
        bot_active = False
        await update.message.reply_text(
            "🔴 <b>Bot Stopped!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "Monitoring paused.\n"
            "Open positions still tracked.\n\n"
            "🟢 Start দিয়ে আবার চালু করো।",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = "🟢 Active" if bot_active else "🔴 Stopped"
        mode = "📄 Paper" if config.paper_trading else "💰 Real"

        sol_balance = 0.0
        if self.wallet.is_loaded():
            sol_balance = await self.wallet.get_sol_balance(self.session)

        positions = self.state.get_open_positions()
        if positions:
            pos_parts = []
            for p in positions:
                age_min = (time.time() - p.entry_time) / 60
                pos_parts.append(f"${p.symbol} ({age_min:.0f}m)")
            pos_info = ", ".join(pos_parts)
        else:
            pos_info = "None"

        pre_stats = self.pre_detector.get_stats()

        msg = (
            f"📊 <b>Bot Status</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n"
            f"Mode: {mode}\n"
            f"SOL Balance: {sol_balance:.4f}\n"
            f"Position: {pos_info}\n"
            f"Pre-Migration: {pre_stats['tracked']} tracked\n"
            f"Learning: {self.learner.model_stats['total_outcomes']} outcomes\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{self.state.format_stats()}"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        trades = self.state.closed_trades[-20:]
        if not trades:
            await update.message.reply_text(
                "📈 কোনো ট্রেড হয়নি এখনো।",
                reply_markup=get_main_keyboard(),
            )
            return

        merged = {}
        for t in trades:
            addr = t.address
            if addr not in merged:
                merged[addr] = {"symbol": t.symbol, "trades": [], "total_pnl_sol": 0, "wins": 0, "losses": 0}
            merged[addr]["trades"].append(t)
            merged[addr]["total_pnl_sol"] += t.pnl_sol
            if t.pnl_sol >= 0:
                merged[addr]["wins"] += 1
            else:
                merged[addr]["losses"] += 1

        total_wins = sum(1 for t in trades if t.pnl_sol >= 0)
        total_losses = len(trades) - total_wins
        win_rate = (total_wins / len(trades) * 100) if trades else 0

        msg = (
            f"📈 <b>Trades Summary</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ {total_wins} WIN | ❌ {total_losses} LOSS\n"
            f"📊 Win Rate: <b>{win_rate:.0f}%</b>\n"
            f"━━━━━━━━━━━━━━━━\n\n"
        )

        for addr, data in list(merged.items())[:10]:
            sym = data["symbol"]
            pnl = data["total_pnl_sol"]
            wins = data["wins"]
            losses = data["losses"]
            emoji = "✅" if pnl >= 0 else "❌"
            count = f"({wins}W/{losses}L)" if len(data["trades"]) > 1 else ""

            t = data["trades"][-1]
            gmgn = get_gmgn_link(addr)
            dex = get_dexscreener_link(addr)

            msg += (
                f"{emoji} <b>${sym}</b> {count}\n"
                f"   PnL: <b>{pnl:+.4f} SOL</b>\n"
                f'   <a href="{gmgn}">GMGN</a> | <a href="{dex}">DexScreener</a>\n\n'
            )

        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from signal_tracker import signal_tracker
        from dataclasses import asdict
        from datetime import datetime, timezone

        all_signals = signal_tracker.signals

        if not all_signals:
            msg = (
                f"📊 <b>সিগনাল রিপোর্ট</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"এখনো কোনো সিগনাল পাওয়া যায়নি।"
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())
            return

        all_dicts = [asdict(s) for s in all_signals]

        tracked = [d for d in all_dicts if d.get("highest_price", 0) > d.get("entry_price", 0) and d.get("entry_price", 0) > 0]
        untracked = [d for d in all_dicts if not (d.get("highest_price", 0) > d.get("entry_price", 0) and d.get("entry_price", 0) > 0)]

        pumped = [d for d in tracked if d.get("max_pnl_pct", 0) > 10]
        not_pumped = [d for d in tracked if d.get("max_pnl_pct", 0) <= 10]

        tp_results = {}
        for tp in [10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200]:
            total = 0
            wins = 0
            for d in tracked:
                entry = d.get("price_at_signal", 0)
                highest = d.get("highest_price", entry)
                if entry > 0 and highest > 0:
                    pump = (highest - entry) / entry * 100
                    if pump >= tp:
                        total += tp
                        wins += 1
                    else:
                        total += max(pump, 0)
            tp_results[tp] = {"profit": total, "wins": wins, "avg": total / len(tracked) if tracked else 0}

        best_tp = max(tp_results.items(), key=lambda x: x[1]["avg"])

        msg = (
            f"📊 <b>সিগনাল রিপোর্ট</b>\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"🎯 মোট সিগনাল: <b>{len(all_dicts)}টি</b>\n"
            f"📈 ট্র্যাক হয়েছে: <b>{len(tracked)}টি</b>\n"
            f"⏳ ট্র্যাক হয়নি: <b>{len(untracked)}টি</b>\n\n"
            f"📈 পাম্প হয়েছে: <b>{len(pumped)}টি</b>\n"
            f"📉 পাম্প হয়নি: <b>{len(not_pumped)}টি</b>\n"
        )

        recent_pumped = pumped[-10:] if len(pumped) > 10 else pumped
        for d in recent_pumped:
            entry = d.get("price_at_signal", 0)
            highest = d.get("highest_price", entry)
            pump = ((highest - entry) / entry * 100) if entry > 0 and highest > 0 else 0
            msg += f"  ✅ ${d.get('symbol', '?')}: <b>+{pump:.0f}%</b>\n"

        if len(pumped) > 10:
            msg += f"  ... এবং আরো {len(pumped) - 10}টি\n"

        msg += (
            f"\n━━━━━━━━━━━━━━━━\n"
            f"💰 <b>সেরা TP (শুধু ট্র্যাক করা সিগনাল):</b>\n\n"
        )

        for tp in [20, 30, 50, 75, 100]:
            r = tp_results.get(tp, {})
            msg += f"  TP +{tp}%: <b>{r.get('wins', 0)}/{len(tracked)} hit</b> → গড় +{r.get('avg', 0):.0f}%\n"

        msg += (
            f"\n🏆 <b>সেরা:</b> TP +{best_tp[0]}% লাগালে "
            f"গড় +{best_tp[1]['avg']:.0f}% লাভ!\n"
            f"\n━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>সাবধান:</b> লো লিকুইডিটি থাকলে\n"
            f"বিক্রি হবে না! লিকুইডিটি > $5K দেখে ট্রেড করো।"
        )

        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sol_balance = 0.0
        if self.wallet.is_loaded():
            sol_balance = await self.wallet.get_sol_balance(self.session)

        pos_value = sum(p.sol_amount for p in self.state.get_open_positions())

        total = sol_balance + pos_value
        pnl = self.state.stats.total_pnl_sol

        msg = (
            f"💰 <b>Balance</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"SOL: <b>{sol_balance:.4f}</b>\n"
            f"Position: <b>{pos_value:.4f}</b>\n"
            f"Total: <b>{total:.4f} SOL</b>\n"
            f"PnL: <b>{pnl:+.4f} SOL</b>\n"
            f"━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        positions = self.state.get_open_positions()
        if not positions:
            await update.message.reply_text(
                "📦 কোনো ওপেন পজিশন নেই।",
                reply_markup=get_main_keyboard(),
            )
            return

        msg = f"📦 <b>Open Positions ({len(positions)}/{config.max_positions})</b>\n━━━━━━━━━━━━━━━━\n"
        for p in positions:
            age_min = (time.time() - p.entry_time) / 60
            tp_pct = ((p.tp_price / p.entry_price) - 1) * 100 if p.entry_price > 0 else 0
            sl_pct = ((p.sl_price / p.entry_price) - 1) * 100 if p.entry_price > 0 else 0
            gmgn = get_gmgn_link(p.address)
            coin_type = self.learner.classify_coin(p.__dict__)

            mcap_lines = ""
            if p.entry_mcap > 0:
                mcap_lines += f"\n💰 Mcap: <b>{format_usd(p.entry_mcap)}</b>"
            if p.tp_mcap > 0:
                mcap_lines += f" → 🎯 <b>{format_usd(p.tp_mcap)}</b>"
            if p.sl_mcap > 0:
                mcap_lines += f" → 🛑 <b>{format_usd(p.sl_mcap)}</b>"

            msg += (
                f"\n<b>${p.symbol}</b> ({p.name}) | {coin_type}\n"
                f"Entry: <b>${p.entry_price:.8f}</b> | SOL: <b>{p.sol_amount:.4f}</b>\n"
                f"TP: <b>${p.tp_price:.8f}</b> ({tp_pct:+.0f}%) | SL: <b>${p.sl_price:.8f}</b> ({sl_pct:+.0f}%)"
                f"{mcap_lines}\n"
                f"Age: <b>{age_min:.0f}m</b> | Score: {p.signal_score:.2f}\n"
                f'🔗 <a href="{gmgn}">GMGN.ai</a>\n'
                f"━━━━━━━━━━━━━━━━\n"
            )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        mode = "Paper" if config.paper_trading else "Real"
        msg = (
            f"⚙️ <b>Configuration</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n"
            f"SOL/Trade: {config.sol_per_trade}\n"
            f"Slippage: {config.max_slippage_bps} bps\n"
            f"Base TP: {config.tp_pct}%\n"
            f"Base SL: {config.sl_pct}%\n"
            f"Scan Interval: {config.scan_interval}s\n"
            f"Vol Spike: {config.volume_spike_multiplier}x\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🚀 Pre-Migration: Active\n"
            f"📝 Auto-Learning: Active\n"
            f"🎯 Dynamic TP/SL: Active"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🔄 Bot restarting...",
            reply_markup=get_main_keyboard(),
        )
        shutdown_event.set()

    async def _cmd_learn(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = self.learner.get_accuracy_report()
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_accuracy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        total = self.learner.model_stats["total_outcomes"]
        if total == 0:
            await update.message.reply_text(
                "📊 এখনো কোনো ট্রেড হয়নি।",
                reply_markup=get_main_keyboard(),
            )
            return

        trend = self.learner.model_stats.get("accuracy_trend", [])
        trend_str = " → ".join(f"{t:.0f}%" for t in trend[-5:]) if trend else "N/A"

        improving = len(trend) >= 2 and trend[-1] > trend[-2] if len(trend) >= 2 else False
        status_emoji = "📈" if improving else "📉"

        msg = (
            f"🎯 <b>Accuracy Report</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Total Trades: {total}\n"
            f"Avg Win: +{self.learner.model_stats['avg_win_pct']:.1f}%\n"
            f"Avg Loss: {self.learner.model_stats['avg_loss_pct']:.1f}%\n"
            f"Patterns Found: {len(self.learner.patterns)}\n"
            f"Coin Profiles: {len(self.learner.coin_profiles)}\n"
            f"Status: {status_emoji} {'Improving' if improving else 'Needs more trades'}\n"
            f"Trend: {trend_str}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📈 Bot নিজে থেকে শিখছে!\n"
            f"প্রতিটি ট্রেডের পর TP/SL অটো আপডেট হয়।"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = self.health.format_status()
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        scan = self.monitor.scan_stats if self.monitor else {}
        pump = self.detector.scan_stats if self.detector else {}
        pre_tracked = len(self.pre_detector.tracked_tokens) if self.pre_detector else 0
        pre_seen = len(self.pre_detector.seen_tokens) if self.pre_detector else 0
        tracked_pumps = getattr(self, "_tracked_pumps", {})
        tracked_count = len(tracked_pumps)

        skip_reasons = pump.get("skip_reasons", {})
        skip_str = "\n".join(f"  {k}: {v}" for k, v in skip_reasons.items()) if skip_reasons else "  None"

        msg = (
            f"🔍 <b>Debug Stats</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<b>DexScreener Monitor:</b>\n"
            f"  Scans: {scan.get('total_scans', 0)}\n"
            f"  Tokens Found: {scan.get('tokens_found', 0)}\n"
            f"  Metrics Fetched: {scan.get('metrics_fetched', 0)}\n"
            f"  Birdeye Tokens: {scan.get('birdeye_tokens', 0)}\n"
            f"\n<b>Pump Detector:</b>\n"
            f"  Scanned: {pump.get('scanned', 0)}\n"
            f"  Filtered: {pump.get('filtered', 0)}\n"
            f"  Detected: {pump.get('detected', 0)}\n"
            f"  Skip Reasons:\n{skip_str}\n"
            f"\n<b>Pre-Migration:</b>\n"
            f"  Seen: {pre_seen}\n"
            f"  Tracked: {pre_tracked}\n"
            f"\n<b>Learning:</b>\n"
            f"  Tracked Pumps: {tracked_count}\n"
            f"━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _cmd_mc100k(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        summary = mcap100k_predictor.get_learning_summary()
        active = len(mcap100k_predictor.active_tokens)

        top_patterns = summary.get("top_patterns", [])
        pattern_lines = []
        for name, pw in top_patterns[:5]:
            wr = pw.get("weight", 0)
            cnt = pw.get("count", 0)
            pattern_lines.append(f"  {name}: {wr:.0%} ({cnt}x)")

        feat_imp = summary.get("feature_importance", {})
        feat_lines = []
        for name, fi in sorted(feat_imp.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True)[:5]:
            wr = fi.get("win_rate", 0)
            cnt = fi.get("count", 0)
            feat_lines.append(f"  {name}: {wr:.0%} ({cnt}x)")

        msg = (
            f"🎯 <b>$100K MC Learning</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Total Tracked: {summary['total_tracked']}\n"
            f"Reached $100K: {summary['reached_100k']}\n"
            f"Reach Rate: {summary['reach_rate']:.0%}\n"
            f"Active Tracking: {active}\n"
            f"Avg Time to $100K: {summary['avg_time_to_100k_seconds']:.0f}s\n"
            f"\n<b>Top Patterns:</b>\n"
            f"{chr(10).join(pattern_lines) if pattern_lines else '  No data yet'}\n"
            f"\n<b>Feature Importance:</b>\n"
            f"{chr(10).join(feat_lines) if feat_lines else '  No data yet'}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()

        if text == "🟢 Start":
            await self._cmd_start(update, context)
        elif text == "🔴 Stop":
            await self._cmd_stop(update, context)
        elif text == "📊 Status":
            await self._cmd_status(update, context)
        elif text == "🚨 Signals":
            await self._cmd_signals(update, context)
        elif text == "⚙️ Config":
            await self._cmd_config(update, context)
        elif text == "🔄 Restart":
            await self._cmd_restart(update, context)
        else:
            await update.message.reply_text(
                "📌 কোন কমান্ড বুঝতে পারিনি।\n\n"
                "বাটন ব্যবহার করো।",
                reply_markup=get_main_keyboard(),
            )

    async def pre_migration_loop(self):
        logger.info("Pre-migration loop started")
        self._recently_migrated = {}
        self._tracked_post_migration = {}
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(10)
                    continue

                url = "https://frontend-api-v3.pump.fun/coins?limit=50&offset=0&sort=created_timestamp&order=DESC"
                try:
                    async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            tokens_with_progress = 0
                            for token in data:
                                if shutdown_event.is_set() or not bot_active:
                                    break

                                mint = token.get("mint")
                                if not mint:
                                    continue

                                sol_raised = float(token.get("real_sol_reserves", 0) or 0) / 1e9
                                complete = token.get("complete", False)
                                symbol = token.get("symbol", "???")
                                name = token.get("name", "Unknown")

                                if complete and mint not in self._recently_migrated:
                                    self._recently_migrated[mint] = {
                                        "symbol": symbol,
                                        "name": name,
                                        "timestamp": time.time(),
                                        "migration_sol": sol_raised,
                                    }
                                    logger.info(
                                        f"🆕 MIGRATED: {symbol} ({name}) | "
                                        f"SOL raised: {sol_raised:.1f}"
                                    )

                                if not complete and sol_raised > 0:
                                    progress = (sol_raised / 85) * 100
                                    if progress >= 1:
                                        tokens_with_progress += 1
                                    if progress >= 70:
                                        logger.info(
                                            f"🚀 PRE-MIGRATION: {symbol} ({name}) | "
                                            f"Progress: {progress:.0f}% | SOL: {sol_raised:.1f}"
                                        )
                                        if self.state.has_open_position():
                                            logger.info(
                                                f"📊 PRE-MIGRATION TRACKING: {symbol} | "
                                                f"Progress: {progress:.0f}% | Position open, tracking for learn"
                                            )
                                            self._track_pump_for_learning({
                                                "symbol": symbol,
                                                "name": name,
                                                "score": min(progress / 100, 0.95),
                                                "signals": [f"Pre-migration {progress:.0f}%", f"SOL: {sol_raised:.1f}"],
                                                "address": mint,
                                                "price_usd": 0,
                                            })
                                        else:
                                            await self._handle_pre_migration(token, progress, sol_raised)

                            if tokens_with_progress > 0:
                                logger.debug(f"Pre-migration scan: {tokens_with_progress} tokens with progress")

                except Exception as e:
                    logger.debug(f"Pre-migration scan error: {e}")

                cutoff = time.time() - 3600
                self._recently_migrated = {
                    k: v for k, v in self._recently_migrated.items()
                    if v["timestamp"] > cutoff
                }

                await self._check_migrated_tokens()

                await asyncio.sleep(20)

            except Exception as e:
                logger.error(f"Pre-migration loop error: {e}")
                await asyncio.sleep(20)

    async def _check_migrated_tokens(self):
        for mint, info in list(self._recently_migrated.items()):
            if shutdown_event.is_set():
                break

            if mint in self._tracked_post_migration:
                age = time.time() - self._tracked_post_migration[mint]["first_seen"]
                if age > 7200:
                    del self._tracked_post_migration[mint]
                    continue
                last_signal = self._tracked_post_migration[mint].get("last_signal_time", 0)
                if time.time() - last_signal < 600:
                    continue
            else:
                self._tracked_post_migration[mint] = {
                    "first_seen": time.time(),
                    "symbol": info["symbol"],
                }

            try:
                metrics = await self.monitor.get_verified_metrics(mint)
                if not metrics:
                    continue

                price = metrics.get("price_usd", 0)
                mcap = metrics.get("fdv", 0)
                liquidity = metrics.get("liquidity", 0)
                age_sec = metrics.get("age_seconds", 0)
                change = metrics.get("price_change_now", 0)

                if price <= 0:
                    continue

                if metrics.get("very_low_liquidity"):
                    logger.debug(f"Skip {info.get('symbol', '?')}: Very low liquidity ${liquidity:.2f}")
                    continue

                lp_count = metrics.get("lp_count", 0)
                if lp_count > 0 and lp_count <= 1:
                    logger.info(f"🚫 Skip {info.get('symbol', '?')}: Single LP (count={lp_count})")
                    continue

                if liquidity < 5000:
                    logger.debug(f"Skip {info.get('symbol', '?')}: Low liquidity ${liquidity:.0f}")
                    continue

                if mcap > 0 and mcap < config.min_mcap_for_trade:
                    continue

                if mcap > config.max_mcap_for_trade:
                    continue

                signals = []
                score = 0

                if change > 50:
                    signals.append(f"Price surge +{change:.0f}%")
                    score += 0.4
                elif change > 20:
                    signals.append(f"Price up +{change:.0f}%")
                    score += 0.25
                elif change > 10:
                    signals.append(f"Price rising +{change:.0f}%")
                    score += 0.15

                if mcap > 0 and mcap < 50000:
                    signals.append(f"Low mcap ${mcap:.0f}")
                    score += 0.2
                elif mcap > 0 and mcap < 200000:
                    signals.append(f"Small mcap ${mcap:.0f}")
                    score += 0.1

                if age_sec < 600:
                    signals.append(f"Just migrated ({age_sec:.0f}s)")
                    score += 0.2

                migration_sol = info.get("migration_sol", 0)
                if migration_sol > 60:
                    signals.append(f"Strong launch ({migration_sol:.0f} SOL)")
                    score += 0.15

                if score >= config.min_pump_score and signals:
                    symbol = info["symbol"]
                    logger.info(
                        f"🎯 POST-MIGRATION PUMP: {symbol} | "
                        f"Score: {score:.2f} | Signals: {', '.join(signals)} | "
                        f"Price: ${price:.8f} | MCap: ${mcap:.0f}"
                    )

                    pump = {
                        "address": mint,
                        "symbol": symbol,
                        "name": info["name"],
                        "price_usd": price,
                        "score": min(score, 1.0),
                        "signals": signals,
                        "metrics": metrics,
                    }

                    if self.state.has_open_position():
                        self._track_pump_for_learning(pump)
                    else:
                        await self.handle_pump(pump)

                    if mint in self._tracked_post_migration:
                        self._tracked_post_migration[mint]["last_signal_time"] = time.time()

            except Exception as e:
                logger.debug(f"Migrated token check error for {mint}: {e}")

    async def _handle_pre_migration(self, token: dict, progress: float, sol_raised: float):
        mint = token.get("mint")
        symbol = token.get("symbol", "???")

        if not hasattr(self, "_pre_migration_cooldown"):
            self._pre_migration_cooldown = {}

        now = time.time()
        if mint in self._pre_migration_cooldown and now - self._pre_migration_cooldown[mint] < 1800:
            return

        score = min(progress / 100, 0.95)

        if progress >= 95:
            score = 0.95
            signals = [f"Migration imminent ({progress:.0f}%)", f"SOL: {sol_raised:.1f}"]
        elif progress >= 85:
            score = 0.85
            signals = [f"Almost migrated ({progress:.0f}%)", f"SOL: {sol_raised:.1f}"]
        else:
            score = 0.75
            signals = [f"Building up ({progress:.0f}%)", f"SOL: {sol_raised:.1f}"]

        self._pre_migration_cooldown[mint] = now

        await self.notifier.notify_signal(
            symbol, score, signals, 0, mint, 0, 0, 0
        )

        self._track_pump_for_learning({
            "symbol": symbol,
            "score": score,
            "signals": signals,
            "address": mint,
            "price_usd": 0,
            "metrics": {"fdv": 0, "liquidity": 0},
        })

    async def monitor_loop(self):
        logger.info("Monitor loop started (post-migration)")
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(5)
                    continue

                new_pairs = await self.monitor.fetch_new_solana_pairs()
                if not new_pairs:
                    await asyncio.sleep(config.scan_interval)
                    continue

                scanned = 0
                for pair in new_pairs[:15]:
                    if shutdown_event.is_set() or not bot_active:
                        break

                    token_addr = pair.get("mint") or pair.get("tokenAddress")
                    if not token_addr:
                        continue

                    metrics = await self.monitor.get_verified_metrics(token_addr, pair)
                    if not metrics:
                        continue

                    if metrics.get("very_low_liquidity"):
                        continue

                    scanned += 1
                    pump = self.detector.detect(metrics)
                    if pump:
                        await self.handle_pump(pump)

                    await asyncio.sleep(1)

                if scanned > 0:
                    stats = self.detector.scan_stats
                    logger.info(
                        f"📊 Scan: {scanned} tokens | "
                        f"Total: {stats.get('scanned',0)} scanned, "
                        f"{stats.get('filtered',0)} filtered, "
                        f"{stats.get('detected',0)} detected | "
                        f"Skip: {stats.get('skip_reasons', {})}"
                    )

                await asyncio.sleep(config.scan_interval)

            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(10)

    async def handle_pump(self, pump: dict):
        metrics = pump["metrics"]

        if pump["score"] < config.min_pump_score:
            logger.debug(f"Pump score {pump['score']:.2f} below min {config.min_pump_score}, skip")
            return

        if metrics.get("fdv", 0) < config.min_mcap_for_trade:
            logger.debug(f"FDV {metrics.get('fdv', 0):.0f} below min {config.min_mcap_for_trade:.0f}, skip")
            return

        holder_count = metrics.get("holders", 0)
        liquidity = metrics.get("liquidity", 0)

        try:
            should_avoid, avoid_reason = signal_tracker.should_avoid(holder_count, liquidity)
            if should_avoid:
                logger.info(f"Skip {pump['symbol']}: {avoid_reason}")
                return
        except Exception as e:
            logger.error(f"should_avoid error: {e}")

        txns = metrics.get("transactions", {})
        m5 = txns.get("m5", {})
        unique_wallets = m5.get("buyers", 0) + m5.get("sellers", 0)

        risk_warnings = pump.get("risk_warnings", [])
        lp_warnings = [w for w in risk_warnings if any(kw in w.lower() for kw in ["wallet", "lp", "single", "liquidity", "holder", "scam", "fake"])]

        if lp_warnings:
            logger.info(f"🚫 SKIP SIGNAL {pump['symbol']}: LP warning - {', '.join(lp_warnings)}")
            return

        coin_type = self.learner.classify_coin(metrics)
        risk_score = pump.get("risk_score", 0)
        if risk_warnings:
            logger.info(f"Coin type: {coin_type} | Symbol: {pump['symbol']} | Risk: {risk_score:.2f} | Warnings: {', '.join(risk_warnings[:2])}")
        else:
            logger.info(f"Coin type: {coin_type} | Symbol: {pump['symbol']}")

        signal = signal_tracker.add_signal(
            symbol=pump["symbol"],
            address=pump["address"],
            score=pump["score"],
            signals=pump["signals"],
            price=pump["price_usd"],
            mcap=metrics.get("fdv", 0),
            holders=holder_count,
            liquidity=liquidity,
            features_snapshot=metrics,
        )

        await self.notifier.notify_signal(
            pump["symbol"], pump["score"], pump["signals"], pump["price_usd"], pump["address"],
            metrics.get("fdv", 0), signal.tp_price, signal.sl_price, pump.get("risk_score", 0),
            unique_wallets=unique_wallets, liquidity=liquidity,
            prob_100k=pump.get("prob_100k", 0)
        )

        self._track_pump_for_learning(pump)

    def _track_pump_for_learning(self, pump: dict):
        try:
            symbol = pump.get("symbol", "???")
            score = pump.get("score", 0)
            signals = pump.get("signals", [])
            address = pump.get("address", "")
            price_usd = pump.get("price_usd", 0)
            metrics = pump.get("metrics", {})

            if score < 0.3:
                return

            coin_type = "unknown"
            if metrics:
                coin_type = self.learner.classify_coin(metrics)

            if not hasattr(self, "_tracked_pumps"):
                self._tracked_pumps = {}

            if address in self._tracked_pumps:
                return

            self._tracked_pumps[address] = {
                "symbol": symbol,
                "score": score,
                "signals": signals,
                "address": address,
                "price_usd": price_usd,
                "last_price": price_usd,
                "coin_type": coin_type,
                "timestamp": time.time(),
                "market_cap": metrics.get("fdv", 0),
                "volume_5m": metrics.get("volume_5m", 0),
                "liquidity": metrics.get("liquidity", 0),
            }

            if len(self._tracked_pumps) > 200:
                oldest = min(self._tracked_pumps.items(), key=lambda x: x[1]["timestamp"])
                del self._tracked_pumps[oldest[0]]

            logger.info(
                f"📝 TRACKED FOR LEARN: {symbol} ({coin_type}) | "
                f"Score: {score:.2f} | Signals: {', '.join(signals)}"
            )

        except Exception as e:
            logger.debug(f"Track pump error: {e}")

    async def trade_loop(self):
        logger.info("Trade loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(2)
                    continue

                positions = self.state.get_open_positions()
                if not positions:
                    await asyncio.sleep(2)
                    continue

                for pos in list(positions):
                    age_minutes = (time.time() - pos.entry_time) / 60
                    is_pre_migration = pos.entry_price == 0 and pos.tp_price == 0

                    if is_pre_migration:
                        await self._monitor_pre_migration_position(pos, age_minutes)
                    else:
                        await self._monitor_normal_position(pos, age_minutes)

                if age_minutes < 5:
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Trade loop error: {e}")
                await asyncio.sleep(5)

    async def _monitor_pre_migration_position(self, pos, age_minutes):
        try:
            url = f"https://frontend-api-v3.pump.fun/coins/{pos.address}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    if age_minutes > 30:
                        logger.warning(f"Pre-migration {pos.symbol} - can't fetch data ({age_minutes:.0f}m old) - closing")
                        await self.close_trade(pos.address, 0, "Data unavailable (30m+)")
                    return

                token = await resp.json()
                complete = token.get("complete", False)
                sol_raised = float(token.get("real_sol_reserves", 0) or 0) / 1e9
                progress = (sol_raised / 85) * 100 if sol_raised > 0 else 0

                if complete:
                    logger.info(
                        f"🔄 MIGRATION COMPLETE: {pos.symbol} | "
                        f"SOL: {sol_raised:.1f} | Age: {age_minutes:.0f}m"
                    )
                    await asyncio.sleep(5)
                    metrics = await self.monitor.get_token_metrics(pos.address)
                    if metrics and metrics["price_usd"] > 0:
                        entry_price = metrics["price_usd"]
                        tp_pct, sl_pct = self.risk.calculate_tp_sl(
                            pos.symbol, metrics, pos.signal_score
                        )
                        pos.entry_price = entry_price
                        pos.tp_price = entry_price * (1 + tp_pct / 100)
                        pos.sl_price = entry_price * (1 - sl_pct / 100)
                        pos.entry_mcap = metrics.get("fdv", 0)
                        pos.tp_mcap = pos.entry_mcap * (1 + tp_pct / 100) if pos.entry_mcap > 0 else 0
                        pos.sl_mcap = pos.entry_mcap * (1 - sl_pct / 100) if pos.entry_mcap > 0 else 0
                        self.state._save_state()
                        logger.info(
                            f"📊 POST-MIGRATION MONITOR: {pos.symbol} | "
                            f"Price: ${entry_price:.8f} | TP: ${pos.tp_price:.8f} | SL: ${pos.sl_price:.8f}"
                        )
                    else:
                        logger.warning(f"Migration complete but no price data for {pos.symbol} - closing")
                        await self.close_trade(pos.address, 0, "Migration - no price data")
                    return

                if age_minutes > 30:
                    logger.warning(
                        f"Pre-migration {pos.symbol} timeout ({age_minutes:.0f}m, progress {progress:.0f}%) - closing"
                    )
                    await self.close_trade(pos.address, 0, f"Pre-migration timeout (30m, {progress:.0f}%)")
                    return

                if progress < 10 and age_minutes > 10:
                    logger.warning(
                        f"Pre-migration {pos.symbol} dying (progress {progress:.0f}%) - closing"
                    )
                    await self.close_trade(pos.address, 0, f"Dying curve ({progress:.0f}%)")
                    return

                logger.debug(
                    f"Pre-migration monitor: {pos.symbol} | "
                    f"Progress: {progress:.0f}% | SOL: {sol_raised:.1f} | Age: {age_minutes:.0f}m"
                )

        except Exception as e:
            logger.debug(f"Pre-migration monitor error for {pos.symbol}: {e}")

    async def _monitor_normal_position(self, pos, age_minutes):
        if age_minutes > 240:
            logger.warning(f"Stale position {pos.symbol} ({age_minutes:.0f}m) - auto closing")
            await self.close_trade(pos.address, pos.entry_price * 0.5, "Stale (4h+)")
            return

        metrics = await self.monitor.get_token_metrics(pos.address)
        if not metrics:
            return

        current_price = metrics["price_usd"]
        current_mcap = metrics.get("fdv", 0)

        if current_price >= pos.tp_price:
            await self.close_trade(pos.address, current_price, "TP Hit")
        elif current_price <= pos.sl_price:
            await self.close_trade(pos.address, current_price, "SL Hit")
        elif pos.sl_mcap > 0 and current_mcap > 0 and current_mcap <= pos.sl_mcap:
            await self.close_trade(pos.address, current_price, "SL Mcap Hit")
        elif pos.tp_mcap > 0 and current_mcap > 0 and current_mcap >= pos.tp_mcap:
            await self.close_trade(pos.address, current_price, "TP Mcap Hit")
        elif time.time() - pos.entry_time > 10800:
            await self.close_trade(pos.address, current_price, "Timeout (3h)")

    async def close_trade(self, address: str, current_price: float, reason: str):
        pos = None
        for p in self.state.get_open_positions():
            if p.address == address:
                pos = p
                break
        if not pos:
            return

        if not config.paper_trading and self.wallet.is_loaded() and pos.token_amount > 0:
            result = await self.executor.sell_token(pos.address, int(pos.token_amount))
            if not result:
                logger.error(f"Sell failed for {pos.symbol}")
                return

        closed = self.state.close_position(address, current_price, reason)
        if closed:
            hold_time = time.time() - pos.entry_time
            coin_type = self.learner.classify_coin(pos.__dict__)

            self.learner.record_trade_outcome(
                symbol=pos.symbol,
                coin_type=coin_type,
                entry_price=pos.entry_price,
                exit_price=current_price,
                hold_time=hold_time,
                tp_pct=((pos.tp_price / pos.entry_price) - 1) * 100 if pos.entry_price > 0 else 0,
                sl_pct=((pos.sl_price / pos.entry_price) - 1) * 100 if pos.entry_price > 0 else 0,
                pump_score=pos.signal_score,
                metrics=pos.__dict__,
                reason=reason,
            )

            emoji = "✅" if closed.pnl_sol >= 0 else "❌"
            logger.info(
                f"{emoji} SELL: {pos.symbol} ({coin_type}) | "
                f"{reason} | PnL: {closed.pnl_sol:+.4f} SOL ({closed.pnl_pct:+.1f}%) | "
                f"Hold: {hold_time:.0f}s"
            )

            exit_mcap = 0
            if pos.entry_mcap > 0 and pos.entry_price > 0:
                exit_mcap = pos.entry_mcap * (current_price / pos.entry_price)

            await self.notifier.notify_sell(
                closed.symbol,
                closed.pnl_sol,
                closed.pnl_pct,
                reason,
                config.paper_trading,
                closed.address,
                entry_mcap=pos.entry_mcap,
                exit_mcap=exit_mcap,
            )

    async def learning_loop(self):
        logger.info("Learning loop started")
        while not shutdown_event.is_set():
            await asyncio.sleep(3600)
            try:
                from signal_tracker import signal_tracker

                closed_signals = [s for s in signal_tracker.signals if s.status == "closed"]
                recorded = set(o.get("address", "") for o in self.learner.trade_outcomes)

                for sig in closed_signals:
                    if sig.address in recorded:
                        continue
                    features = sig.features_snapshot if sig.features_snapshot else {}
                    metrics = {
                        "fdv": sig.mcap_at_signal,
                        "liquidity": sig.liquidity,
                        "volume_5m": features.get("volume_5m", 0),
                        "volume_1h": features.get("volume_1h", 0),
                        "price_change_5m": features.get("price_change_5m", 0),
                        "price_change_1h": features.get("price_change_1h", 0),
                        "age_seconds": features.get("age_seconds", time.time() - sig.timestamp),
                        "liquidity_change": features.get("liquidity_change", 0),
                        "volume_change": features.get("volume_change", 0),
                        "holders": sig.holders,
                    }
                    self.learner.record_signal_outcome(
                        symbol=sig.symbol,
                        address=sig.address,
                        score=sig.score,
                        signals=sig.signals,
                        entry_price=sig.entry_price,
                        highest_price=sig.highest_price,
                        lowest_price=sig.lowest_price,
                        current_price=sig.current_price,
                        metrics=metrics,
                    )

                self.learner.cleanup_old_data(max_age_days=30)
                stats = self.learner.model_stats
                indicators = self.learner.get_pre_pump_indicators({})
                logger.info(
                    f"Learning Stats: {stats['total_outcomes']} outcomes | "
                    f"Avg Win: {stats['avg_win_pct']:.1f}% | "
                    f"Patterns: {len(self.learner.patterns)} | "
                    f"Win Rate: {indicators.get('win_rate', 0):.0f}%"
                )
            except Exception as e:
                logger.error(f"Learning loop error: {e}")

    async def health_loop(self):
        logger.info("Health loop started")
        while not shutdown_event.is_set():
            await asyncio.sleep(120)
            try:
                if self.session:
                    await self.health.check_api(self.session)

                if self.health.should_reconnect():
                    logger.warning("API failures detected, attempting reconnect...")
                    reconnect_ok = await self.health.attempt_reconnect()
                    if reconnect_ok:
                        logger.info("Reconnect successful")
                    else:
                        logger.warning("Reconnect failed, will retry...")
                        if time.time() - getattr(self, '_last_error_notify', 0) > 600:
                            await self.notifier.notify_error("API connection issues detected. Will retry in 10 min...")
                            self._last_error_notify = time.time()

            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def signal_monitor_loop(self):
        logger.info("Signal monitor loop started")
        while not shutdown_event.is_set():
            try:
                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                now = time.time()

                for sig in active:
                    if shutdown_event.is_set():
                        break

                    try:
                        age_min = (now - sig.timestamp) / 60

                        if age_min > 60:
                            signal_tracker.close_signal(sig.address, sig.current_price)
                            self._record_signal_outcome(sig)
                            logger.info(f"⏰ TIMEOUT CLOSE: {sig.symbol} | PnL: {sig.final_pnl_pct:.1f}% after {age_min:.0f}m")
                            continue

                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics and metrics.get("price_usd", 0) > 0:
                            updated = signal_tracker.update_price(sig.address, metrics["price_usd"])
                            if updated:
                                if updated.tp_hit and not sig.tp_hit:
                                    signal_tracker.close_signal(sig.address, updated.current_price)
                                    self._record_signal_outcome(updated)
                                    logger.info(f"🎯 TP HIT: {sig.symbol} | PnL: {updated.final_pnl_pct:.1f}%")
                                elif updated.sl_hit and not sig.sl_hit:
                                    signal_tracker.close_signal(sig.address, updated.current_price)
                                    self._record_signal_outcome(updated)
                                    logger.info(f"🛑 SL HIT: {sig.symbol} | PnL: {updated.final_pnl_pct:.1f}%")
                    except Exception as e:
                        logger.debug(f"Signal monitor error for {sig.symbol}: {e}")

                    await asyncio.sleep(1)

                await asyncio.sleep(15)

            except Exception as e:
                logger.error(f"Signal monitor loop error: {e}")
                await asyncio.sleep(30)

    def _record_signal_outcome(self, signal):
        try:
            from signal_tracker import signal_tracker
            from learner import Learner

            if not hasattr(self, '_outcome_recorded'):
                self._outcome_recorded = set()

            if signal.address in self._outcome_recorded:
                return
            self._outcome_recorded.add(signal.address)

            if len(self._outcome_recorded) > 1000:
                self._outcome_recorded = set(list(self._outcome_recorded)[-500:])

            features = signal.features_snapshot if signal.features_snapshot else {}
            metrics = {
                "liquidity": signal.liquidity,
                "fdv": signal.mcap_at_signal,
                "volume_5m": features.get("volume_5m", 0),
                "volume_1h": features.get("volume_1h", 0),
                "price_change_5m": features.get("price_change_5m", 0),
                "price_change_1h": features.get("price_change_1h", 0),
                "age_seconds": time.time() - signal.timestamp,
                "holders": signal.holders,
                "liquidity_change": features.get("liquidity_change", 0),
                "volume_change": features.get("volume_change", 0),
            }

            self.learner.record_signal_outcome(
                symbol=signal.symbol,
                address=signal.address,
                score=signal.score,
                signals=signal.signals,
                entry_price=signal.entry_price,
                highest_price=signal.highest_price,
                lowest_price=signal.lowest_price,
                current_price=signal.current_price,
                metrics=metrics,
                signal_type=signal.signal_type,
            )
        except Exception as e:
            logger.error(f"Record signal outcome error: {e}")

    async def lifetime_pump_loop(self):
        logger.info("Lifetime pump loop started")
        lifetime_cooldown: dict = {}
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(10)
                    continue

                tracked = getattr(self, "_tracked_pumps", {})
                if not tracked:
                    await asyncio.sleep(30)
                    continue

                now = time.time()
                for addr, info in list(tracked.items()):
                    if shutdown_event.is_set():
                        break

                    try:
                        if addr in lifetime_cooldown and now - lifetime_cooldown[addr] < 1800:
                            continue

                        metrics = await self.monitor.get_verified_metrics(addr)
                        if not metrics or metrics.get("price_usd", 0) <= 0:
                            continue

                        if metrics.get("very_low_liquidity"):
                            continue

                        liquidity = metrics.get("liquidity", 0)
                        if liquidity < config.min_liquidity:
                            continue

                        txns = metrics.get("transactions", {})
                        m5 = txns.get("m5", {})
                        unique_wallets = m5.get("buyers", 0) + m5.get("sellers", 0)
                        if unique_wallets < 10:
                            continue

                        old_price = info.get("last_price", 0)
                        new_price = metrics["price_usd"]

                        if old_price > 0 and new_price > old_price * 1.3:
                            pump_pct = (new_price - old_price) / old_price * 100
                            score = min(pump_pct / 100, 1.0)
                            if score < config.min_pump_score:
                                continue

                            logger.info(
                                f"🚀 LIFETIME PUMP: {info.get('symbol', '?')} | "
                                f"+{pump_pct:.0f}% | Old: ${old_price:.8f} → New: ${new_price:.8f}"
                            )

                            signal = signal_tracker.add_signal(
                                symbol=info.get("symbol", "???"),
                                address=addr,
                                score=score,
                                signals=[f"Lifetime pump +{pump_pct:.0f}%"],
                                price=new_price,
                                mcap=metrics.get("fdv", 0),
                                holders=unique_wallets,
                                liquidity=liquidity,
                                features_snapshot=metrics,
                            )

                            if signal.address == addr and signal.status == "active":
                                lifetime_cooldown[addr] = now
                                await self.notifier.notify_signal(
                                    info.get("symbol", "???"), signal.score, signal.signals,
                                    new_price, addr, metrics.get("fdv", 0),
                                    signal.tp_price, signal.sl_price
                                )

                        info["last_price"] = new_price

                    except Exception as e:
                        logger.debug(f"Lifetime pump check error for {addr}: {e}")

                    await asyncio.sleep(2)

                if len(lifetime_cooldown) > 500:
                    cutoff = now - 3600
                    lifetime_cooldown = {k: v for k, v in lifetime_cooldown.items() if v > cutoff}

                await asyncio.sleep(15)

            except Exception as e:
                logger.error(f"Lifetime pump loop error: {e}")
                await asyncio.sleep(30)

    async def price_history_loop(self):
        logger.info("Price history loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(5)
                    continue

                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                for sig in active:
                    try:
                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics and metrics.get("price_usd", 0) > 0:
                            price_history.record(
                                sig.address,
                                metrics["price_usd"],
                                metrics.get("volume_5m", 0),
                                metrics.get("liquidity", 0),
                                metrics.get("fdv", 0),
                            )
                            price_history.update_metadata(
                                sig.address,
                                symbol=sig.symbol,
                            )
                    except Exception as e:
                        logger.debug(f"Price history record error for {sig.symbol}: {e}")

                if hasattr(self, '_tracked_pumps'):
                    for addr in list(self._tracked_pumps.keys())[:50]:
                        try:
                            metrics = await self.monitor.get_token_metrics(addr)
                            if metrics and metrics.get("price_usd", 0) > 0:
                                price_history.record(
                                    addr,
                                    metrics["price_usd"],
                                    metrics.get("volume_5m", 0),
                                    metrics.get("liquidity", 0),
                                    metrics.get("fdv", 0),
                                )
                        except Exception:
                            pass

                price_history.cleanup()
                await asyncio.sleep(config.price_snapshot_interval)

            except Exception as e:
                logger.error(f"Price history loop error: {e}")
                await asyncio.sleep(30)

    async def backtest_loop(self):
        logger.info("Backtest loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.backtest_enabled:
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(60)

                backtested = backtest_engine.run_backtest_cycle(price_history)
                if backtested > 0:
                    report = backtest_engine.get_accuracy_report()
                    logger.info(
                        f"Backtest cycle: {report['total']} results | "
                        f"Win rate: {report['win_rate']:.1f}% | "
                        f"Avg PnL: {report['avg_pnl']:.1f}%"
                    )

                await asyncio.sleep(config.backtest_interval)

            except Exception as e:
                logger.error(f"Backtest loop error: {e}")
                await asyncio.sleep(60)

    async def pattern_analysis_loop(self):
        logger.info("Pattern analysis loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.backtest_enabled:
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(300)

                if len(backtest_engine.results) >= 10:
                    new_patterns = pattern_analyzer.analyze_backtest_results(backtest_engine.results)
                    pattern_analyzer.update_signal_correlations(backtest_engine.results)

                    report = pattern_analyzer.get_accuracy_report()
                    logger.info(
                        f"Pattern analysis: {report['total_patterns']} patterns | "
                        f"Profitable: {report['profitable_patterns']} | "
                        f"Avg success: {report['avg_success_rate']:.1%}"
                    )

                await asyncio.sleep(config.pattern_analysis_interval)

            except Exception as e:
                logger.error(f"Pattern analysis loop error: {e}")
                await asyncio.sleep(60)

    async def weight_optimization_loop(self):
        logger.info("Weight optimization loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.backtest_enabled:
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(600)

                if len(backtest_engine.results) >= 20:
                    new_weights = weight_optimizer.optimize_weights(
                        backtest_engine,
                        self.detector.scorer.signal_weights
                    )
                    self.detector.scorer.signal_weights = new_weights

                    new_min = weight_optimizer.optimize_min_quality(
                        backtest_engine,
                        self.detector.scorer.min_quality
                    )
                    self.detector.scorer.min_quality = new_min

                    report = weight_optimizer.get_optimization_report()
                    logger.info(
                        f"Weight optimization: {report['latest_changes']} adjustments | "
                        f"Increases: {report['increases']} | "
                        f"Decreases: {report['decreases']}"
                    )

                await asyncio.sleep(config.weight_optimization_interval)

            except Exception as e:
                logger.error(f"Weight optimization loop error: {e}")
                await asyncio.sleep(60)

    async def wallet_tracking_loop(self):
        logger.info("Wallet tracking loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.wallet_tracking_enabled:
                    await asyncio.sleep(60)
                    continue

                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                for sig in active:
                    try:
                        wallet_tracker.update_wallet_profile(
                            sig.address,
                            category="signal_target",
                            metadata={"symbol": sig.symbol, "score": sig.score}
                        )
                    except Exception as e:
                        logger.debug(f"Wallet tracking error: {e}")

                wallet_tracker.cleanup_old_data()
                await asyncio.sleep(config.wallet_tracking_interval)

            except Exception as e:
                logger.error(f"Wallet tracking loop error: {e}")
                await asyncio.sleep(60)

    async def social_sentiment_loop(self):
        logger.info("Social sentiment loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.social_sentiment_enabled:
                    await asyncio.sleep(60)
                    continue

                await social_sentiment_engine.fetch_twitter_trending()
                alerts = social_sentiment_engine.detect_trends()
                if alerts:
                    for alert in alerts[:3]:
                        logger.info(f"SOCIAL ALERT: {alert.alert_type} for {alert.token}: {alert.message}")

                social_sentiment_engine.cleanup_old_data()
                await asyncio.sleep(config.social_sentiment_interval)

            except Exception as e:
                logger.error(f"Social sentiment loop error: {e}")
                await asyncio.sleep(60)

    async def chain_reaction_loop(self):
        logger.info("Chain reaction loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.chain_reaction_enabled:
                    await asyncio.sleep(60)
                    continue

                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                for sig in active:
                    try:
                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics:
                            price = metrics.get("price_usd", 0)
                            if price > 0:
                                chain_reaction_analyzer.update_price_history(sig.symbol, price)
                    except Exception as e:
                        logger.debug(f"Chain reaction update error: {e}")

                events = chain_reaction_analyzer.detect_chain_reactions()
                if events:
                    for event in events[:3]:
                        logger.info(f"CHAIN REACTION: {event.event_type} for {event.trigger_token}: {event.description}")

                await asyncio.sleep(config.chain_reaction_interval)

            except Exception as e:
                logger.error(f"Chain reaction loop error: {e}")
                await asyncio.sleep(60)

    async def market_regime_loop(self):
        logger.info("Market regime loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.market_regime_enabled:
                    await asyncio.sleep(60)
                    continue

                analysis = simple_regime_detector.analyze_regime()
                if analysis:
                    logger.info(
                        f"Market regime: {analysis.regime} | "
                        f"Confidence: {analysis.confidence:.2f} | "
                        f"Risk: {analysis.risk_assessment}"
                    )

                simple_regime_detector.cleanup_old_data()
                await asyncio.sleep(config.market_regime_interval)

            except Exception as e:
                logger.error(f"Market regime loop error: {e}")
                await asyncio.sleep(60)

    async def technical_indicators_loop(self):
        logger.info("Technical indicators loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.technical_indicators_enabled:
                    await asyncio.sleep(60)
                    continue

                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                for sig in active:
                    try:
                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics:
                            price = metrics.get("price_usd", 0)
                            if price > 0:
                                technical_indicator_engine.update_price_history(
                                    sig.symbol,
                                    [price],
                                    [time.time()]
                                )
                                analysis = technical_indicator_engine.get_indicator_analysis(sig.symbol)
                                if analysis and analysis.get('overall_signal') != 'neutral':
                                    logger.info(
                                        f"TECHNICAL {sig.symbol}: {analysis['overall_signal']} "
                                        f"(confidence: {analysis['confidence']:.2f})"
                                    )
                    except Exception as e:
                        logger.debug(f"Technical indicators error: {e}")

                technical_indicator_engine.cleanup_old_data()
                await asyncio.sleep(config.technical_indicators_interval)

            except Exception as e:
                logger.error(f"Technical indicators loop error: {e}")
                await asyncio.sleep(60)

    async def neural_engine_loop(self):
        logger.info("Neural engine loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.neural_engine_enabled:
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(600)

                training_data = {}
                for sig in signal_tracker.get_active_signals()[:20]:
                    try:
                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics:
                            training_data[sig.symbol] = [metrics]
                    except Exception:
                        continue

                if training_data:
                    neural_engine.train_models(training_data)

                await asyncio.sleep(config.neural_retrain_interval)

            except Exception as e:
                logger.error(f"Neural engine loop error: {e}")
                await asyncio.sleep(60)

    async def ensemble_loop(self):
        logger.info("Ensemble learning loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active or not config.ensemble_enabled:
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(600)

                if len(neural_engine.models) >= 2:
                    for model_name, model_info in neural_engine.models.items():
                        ensemble_learner.register_base_model(model_name, model_info.model)

                    training_samples = ensemble_learner.generate_training_samples({})
                    if training_samples:
                        ensemble_learner.train_ensemble_models(training_samples)

                await asyncio.sleep(config.ensemble_retrain_interval)

            except Exception as e:
                logger.error(f"Ensemble loop error: {e}")
                await asyncio.sleep(60)


    async def mcap100k_tracking_loop(self):
        logger.info("$100K MC tracking loop started")
        while not shutdown_event.is_set():
            try:
                if not bot_active:
                    await asyncio.sleep(30)
                    continue

                from signal_tracker import signal_tracker
                active = signal_tracker.get_active_signals()
                for sig in active:
                    try:
                        metrics = await self.monitor.get_token_metrics(sig.address)
                        if metrics:
                            result = mcap100k_predictor.update_token_metrics(sig.address, metrics)
                            if result:
                                logger.info(
                                    f"$100K RESULT: {result['symbol']} | "
                                    f"Reached: {result['reached_100k']} | "
                                    f"Peak: ${result['peak_mcap']:,.0f}"
                                )
                    except Exception as e:
                        logger.debug(f"MCap100k tracking error: {e}")

                mcap100k_predictor.prune_weak_patterns()

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"MCap100k tracking loop error: {e}")
                await asyncio.sleep(60)


async def main():
    bot = TradingBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
