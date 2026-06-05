import unittest
import asyncio
import os
import importlib
from unittest.mock import AsyncMock, MagicMock, patch


class TestHoneypotDetector(unittest.TestCase):

    def setUp(self):
        from honeypot_detector import HoneypotDetector, HoneypotReport
        self.HoneypotDetector = HoneypotDetector
        self.HoneypotReport = HoneypotReport

    def test_honeypot_via_rugcheck(self):
        async def run():
            session = MagicMock()
            rug = MagicMock()
            rug.is_risky = True
            rug.risks = ["Honeypot", "Freeze Authority still enabled"]
            rug.lp_locked = 0
            rug.score = 5000
            rugcheck = MagicMock()
            rugcheck.check_token = AsyncMock(return_value=rug)
            helius = MagicMock()
            dex = MagicMock()
            detector = self.HoneypotDetector(session, rugcheck=rugcheck, helius=helius, dex=dex)
            report = await detector.check("BLOOD_HONEYPOT_ADDRESS_xxxxxxxxxxxxxxxx", "$BLOOD")
            self.assertTrue(report.is_honeypot)
            self.assertGreater(report.confidence, 0.8)
            self.assertTrue(any("Honeypot" in r for r in report.reasons))
        asyncio.run(run())

    def test_clean_token_passes(self):
        async def run():
            session = MagicMock()
            rug = MagicMock()
            rug.is_risky = False
            rug.risks = []
            rug.lp_locked = 80
            rug.score = 100
            rugcheck = MagicMock()
            rugcheck.check_token = AsyncMock(return_value=rug)
            helius = MagicMock()
            helius.get_top_holders = AsyncMock(return_value=[
                {"amount": 50}, {"amount": 30}, {"amount": 20}, {"amount": 15},
                {"amount": 10}, {"amount": 8}, {"amount": 7}, {"amount": 5},
                {"amount": 3}, {"amount": 2}
            ])
            dex = MagicMock()
            detector = self.HoneypotDetector(session, rugcheck=rugcheck, helius=helius, dex=dex)
            pair = {"top10HolderPercent": 5, "liquidity": {"usd": 5000}}
            report = await detector.check("CLEAN_TOKEN_ADDRESS_xxxxxxxxxxxxxxxxxx", "GOOD", pair=pair)
            self.assertFalse(report.is_honeypot)
        asyncio.run(run())

    def test_top10_concentration_flags_honeypot(self):
        async def run():
            session = MagicMock()
            rug = MagicMock()
            rug.is_risky = False
            rug.risks = []
            rug.lp_locked = 50
            rug.score = 200
            rugcheck = MagicMock()
            rugcheck.check_token = AsyncMock(return_value=rug)
            helius = MagicMock()
            helius.get_top_holders = AsyncMock(return_value=[
                {"amount": 5000}, {"amount": 1000}
            ])
            dex = MagicMock()
            detector = self.HoneypotDetector(session, rugcheck=rugcheck, helius=helius, dex=dex)
            pair = {"top10HolderPercent": 45}
            report = await detector.check("CONCENTRATED_ADDR_xxxxxxxxxxxxxxxxxxxxx", "BUNDLE", pair=pair)
            self.assertTrue(report.is_honeypot)
            self.assertGreater(report.top10_pct, 30)
        asyncio.run(run())

    def test_micro_liquidity_flags_honeypot(self):
        async def run():
            session = MagicMock()
            rug = MagicMock()
            rug.is_risky = False
            rug.risks = []
            rug.lp_locked = 0
            rug.score = 200
            rugcheck = MagicMock()
            rugcheck.check_token = AsyncMock(return_value=rug)
            helius = MagicMock()
            helius.get_top_holders = AsyncMock(return_value=[])
            dex = MagicMock()
            detector = self.HoneypotDetector(session, rugcheck=rugcheck, helius=helius, dex=dex)
            pair = {"liquidity": {"usd": 50}}
            report = await detector.check("MICRO_LIQ_ADDR_xxxxxxxxxxxxxxxxxxxxx", "MICRO", pair=pair)
            self.assertFalse(report.tradable)
        asyncio.run(run())


class TestBotStateHoneypot(unittest.TestCase):

    def setUp(self):
        from bot_state import BotState
        self.state = BotState()

    def test_mark_and_check_honeypot(self):
        async def run():
            await self.state.mark_honeypot("ADDR_HONEY_xxxxxxxxxxxxxxxxxxxxxxxxx")
            self.assertTrue(await self.state.is_honeypot("ADDR_HONEY_xxxxxxxxxxxxxxxxxxxxxxxxx"))
            self.assertTrue(await self.state.is_blacklisted("ADDR_HONEY_xxxxxxxxxxxxxxxxxxxxxxxxx"))
        asyncio.run(run())

    def test_blocked_deployer(self):
        async def run():
            await self.state.add_blocked_deployer("DEPLOYER_BAD_xxxxxxxxxxxxxxxxxxxxxxxxx")
            self.assertTrue(await self.state.is_deployer_blocked("DEPLOYER_BAD_xxxxxxxxxxxxxxxxxxxxxxxxx"))
            self.assertFalse(await self.state.is_deployer_blocked("DEPLOYER_GOOD_xxxxxxxxxxxxxxxxxxxxxxxx"))
            self.assertFalse(await self.state.is_deployer_blocked(""))
        asyncio.run(run())

    def test_empty_deployer_safe(self):
        async def run():
            self.assertFalse(await self.state.is_deployer_blocked(""))
            await self.state.add_blocked_deployer("")
        asyncio.run(run())

    def test_stats_include_new_fields(self):
        async def run():
            stats = await self.state.get_stats()
            self.assertIn("blocked_deployers", stats)
            self.assertIn("honeypot_addresses", stats)
        asyncio.run(run())


class TestSignalFilterThreshold(unittest.TestCase):

    def setUp(self):
        self._test_data = "./test_threshold_data.json"
        for f in [self._test_data, "./test_bot_data.json", "./bot_data.json", "./test_learner_data.json"]:
            if os.path.exists(f):
                os.remove(f)
        os.environ["DATA_FILE"] = self._test_data
        import config
        config.config.data_file = self._test_data
        import learner
        learner.DATA_FILE = self._test_data
        importlib.reload(learner)
        import signal_filter
        importlib.reload(signal_filter)
        self.SignalFilter = signal_filter.SignalFilter
        self.filter = signal_filter.SignalFilter()
        self.learner = learner

    def tearDown(self):
        for f in [self._test_data, "./test_bot_data.json", "./bot_data.json", "./test_learner_data.json"]:
            if os.path.exists(f):
                os.remove(f)

    def test_min_threshold_is_70(self):
        self.assertEqual(self.filter.min_threshold, 0.70)

    def test_warmup_threshold_lifts_to_55(self):
        self.assertTrue(self.filter._warmup_active())
        self.assertEqual(self.filter.effective_threshold(), 0.55)

    def test_effective_threshold_after_signals(self):
        data = self.learner.load_data()
        for i in range(25):
            data["model"].setdefault("signal_results", []).append({
                "address": f"addr_{i}", "symbol": f"S{i}", "verdict": "PUMP",
                "multiplier": 3.0, "social_score": 0, "timestamp": "2026-01-01T00:00:00Z"
            })
        self.learner.save_data(data)
        self.assertFalse(self.filter._warmup_active())
        self.assertEqual(self.filter.effective_threshold(), 0.70)


class TestAutoLearnHoneypot(unittest.TestCase):

    def setUp(self):
        self._test_data = "./test_honeypot_al.json"
        for f in [self._test_data, "./test_auto_learn_data.json", "./bot_data.json"]:
            if os.path.exists(f):
                os.remove(f)
        os.environ["DATA_FILE"] = self._test_data
        import config
        config.config.data_file = self._test_data
        import learner
        learner.DATA_FILE = self._test_data
        importlib.reload(learner)
        self.learner = learner

    def tearDown(self):
        for f in [self._test_data, "./test_auto_learn_data.json", "./bot_data.json"]:
            if os.path.exists(f):
                os.remove(f)

    def test_honeypot_pump_recorded_as_dump(self):
        from learner import auto_learn_from_tracking
        learned, kind, msg = auto_learn_from_tracking(
            address="BLOOD_HONEYPOT_ADDR_xxxxxxxxxxxxxxxxxxxxx",
            symbol="$BLOOD",
            name="Blood Coin",
            launch_dict={"buy_count": 100, "sell_count": 5, "unique_wallets": 50, "volume": 1000.0},
            current_price=11.0,
            initial_price=1.0,
            holders=145,
            age_seconds=900,
            pump_threshold=3.0,
            is_honeypot=True,
            honeypot_reasons=["GMGN: Token Frozen blacklist enabled", "rugcheck: Honeypot"],
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "honeypot_dump")
        data = self.learner.load_data()
        self.assertEqual(len(data["pump_patterns"]), 0)
        self.assertEqual(len(data["dump_patterns"]), 1)
        self.assertTrue(data["dump_patterns"][0]["honeypot"])

    def test_real_pump_above_threshold(self):
        from learner import auto_learn_from_tracking
        learned, kind, msg = auto_learn_from_tracking(
            address="REAL_PUMP_ADDR_xxxxxxxxxxxxxxxxxxxxxxxxx",
            symbol="PUMP",
            name="Real Pump",
            launch_dict={"buy_count": 100, "sell_count": 5, "unique_wallets": 50, "volume": 1000.0},
            current_price=4.0,
            initial_price=1.0,
            holders=50,
            age_seconds=900,
            pump_threshold=3.0,
            is_honeypot=False,
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "pump")
        data = self.learner.load_data()
        self.assertEqual(len(data["pump_patterns"]), 1)

    def test_purge_honeypot_patterns(self):
        from learner import auto_learn_from_tracking, purge_honeypot_patterns, load_data
        auto_learn_from_tracking(
            address="FAKE_PUMP_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            symbol="FAKE",
            name="Fake",
            launch_dict={"buy_count": 50, "sell_count": 1, "unique_wallets": 30, "volume": 500.0},
            current_price=10.0,
            initial_price=1.0,
            age_seconds=900,
        )
        data = load_data()
        self.assertEqual(len(data["pump_patterns"]), 1)
        result = purge_honeypot_patterns({"FAKE_PUMP_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"})
        self.assertEqual(result["moved"], 1)
        data = load_data()
        self.assertEqual(len(data["pump_patterns"]), 0)
        self.assertGreaterEqual(len(data["dump_patterns"]), 1)

    def test_purge_safe_no_op_when_no_blocklist(self):
        from learner import auto_learn_from_tracking, purge_honeypot_patterns, load_data
        auto_learn_from_tracking(
            address="REAL_PUMP_KEEP_xxxxxxxxxxxxxxxxxxxxx",
            symbol="REAL",
            name="Real",
            launch_dict={"buy_count": 50, "sell_count": 1, "unique_wallets": 30, "volume": 500.0},
            current_price=4.0,
            initial_price=1.0,
            age_seconds=900,
        )
        result = purge_honeypot_patterns(set())
        self.assertEqual(result["moved"], 0)
        data = load_data()
        self.assertEqual(len(data["pump_patterns"]), 1)


if __name__ == "__main__":
    unittest.main()
