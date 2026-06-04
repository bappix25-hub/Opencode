import unittest
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
from signal_filter import SignalFilter
from verify_loop import VerifyLoop


class TestSignalFilter(unittest.TestCase):

    def setUp(self):
        for f in ["./golden_patterns.json", "./blacklist_patterns.json",
                  "./test_golden.json", "./test_blacklist.json",
                  "test_learner_data.json", "test_bot_data.json",
                  "bot_data.json", "backtest_summary.md"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        os.environ["DATA_FILE"] = "./test_filter_data.json"
        from config import config
        config.data_file = "./test_filter_data.json"
        self.filter = SignalFilter()

    def tearDown(self):
        for f in ["./golden_patterns.json", "./blacklist_patterns.json",
                  "./test_filter_data.json", "test_filter_data.json"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_initial_state(self):
        self.assertIsInstance(self.filter.golden_patterns, dict)
        self.assertIsInstance(self.filter.blacklist, dict)
        self.assertEqual(self.filter.min_threshold, 0.60)

    def test_calculate_timing_score_sweet_spot(self):
        score = self.filter.calculate_timing_score(300)
        self.assertEqual(score, 1.0)
        score = self.filter.calculate_timing_score(120)
        self.assertEqual(score, 1.0)
        score = self.filter.calculate_timing_score(600)
        self.assertEqual(score, 1.0)

    def test_calculate_timing_score_extended(self):
        score = self.filter.calculate_timing_score(60)
        self.assertEqual(score, 0.7)
        score = self.filter.calculate_timing_score(1200)
        self.assertEqual(score, 0.7)

    def test_calculate_timing_score_outside(self):
        score = self.filter.calculate_timing_score(30)
        self.assertLess(score, 0.7)
        score = self.filter.calculate_timing_score(3600)
        self.assertLess(score, 0.5)

    def test_calculate_onchain_score(self):
        pattern = {"mcap": 100000, "liquidity": 5000, "vol_liq_ratio": 0.3}
        score = self.filter.calculate_onchain_score(pattern, {}, 0.6)
        self.assertGreater(score, 0.6)

    def test_should_signal_below_threshold(self):
        pattern = {"mcap": 100, "liquidity": 100, "vol_liq_ratio": 0.01}
        should, score, reason = self.filter.should_signal(
            "test_addr", pattern, ai_score=0.3,
            social_score=0.2, age_seconds=300
        )
        self.assertFalse(should)
        self.assertLess(score, 0.60)

    def test_should_signal_above_threshold(self):
        pattern = {"mcap": 50000, "liquidity": 5000, "vol_liq_ratio": 0.5}
        should, score, reason = self.filter.should_signal(
            "test_addr_2", pattern, ai_score=0.8,
            social_score=0.7, age_seconds=300
        )
        self.assertTrue(should)
        self.assertGreaterEqual(score, 0.60)

    def test_should_signal_blacklisted(self):
        self.filter.add_to_blacklist("blocked_addr", "test")
        pattern = {"mcap": 50000, "liquidity": 5000, "vol_liq_ratio": 0.5}
        should, score, reason = self.filter.should_signal(
            "blocked_addr", pattern, ai_score=0.9,
            social_score=0.9, age_seconds=300
        )
        self.assertFalse(should)
        self.assertIn("Blacklisted", reason)

    def test_promote_to_golden(self):
        pattern = {"mcap": 100000, "liquidity": 5000, "vol_liq_ratio": 0.3}
        self.filter.promote_to_golden("TEST", pattern, 6.0)
        self.assertEqual(len(self.filter.golden_patterns.get("patterns", [])), 1)

    def test_promote_to_golden_repeated(self):
        pattern = {"mcap": 100000, "liquidity": 5000, "vol_liq_ratio": 0.3}
        self.filter.promote_to_golden("TEST", pattern, 5.5)
        self.filter.promote_to_golden("TEST", pattern, 7.0)
        self.assertEqual(len(self.filter.golden_patterns.get("patterns", [])), 1)
        gp = self.filter.golden_patterns["patterns"][0]
        self.assertEqual(gp["count"], 2)
        self.assertEqual(gp["max_multiplier"], 7.0)

    def test_promote_below_5x_ignored(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        self.filter.promote_to_golden("TEST", pattern, 3.0)
        self.assertEqual(len(self.filter.golden_patterns.get("patterns", [])), 0)

    def test_record_signal_result_dump(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        self.filter.record_signal_result("addr_dump_unique_1", "SYMD1", pattern, 1.5)
        stats = self.filter.get_stats()
        self.assertGreaterEqual(stats["total_signals"], 1)
        self.assertEqual(stats["successful"], 0)

    def test_record_signal_result_pump(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        before = self.filter.get_stats()["total_signals"]
        self.filter.record_signal_result("addr_pump_unique_2", "SYMP2", pattern, 3.5)
        stats = self.filter.get_stats()
        self.assertEqual(stats["total_signals"], before + 1)
        self.assertGreaterEqual(stats["successful"], 1)

    def test_record_signal_result_strong_pump(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        before_strong = self.filter.get_stats()["strong_pumps"]
        self.filter.record_signal_result("addr_strong_unique_3", "SYMS3", pattern, 6.0)
        stats = self.filter.get_stats()
        self.assertEqual(stats["strong_pumps"], before_strong + 1)

    def test_blacklist_after_3_dumps_same_address(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        same_addr = "blacklist_test_addr_xyz"
        for i in range(3):
            self.filter.record_signal_result(same_addr, "BLTEST", pattern, 1.0)
        self.assertGreater(len(self.filter.blacklist.get("patterns", [])), 0)

    def test_golden_match_score(self):
        pattern = {"mcap": 100000, "liquidity": 5000}
        self.filter.promote_to_golden("GLD", pattern, 5.5)
        match = self.filter.is_golden_match(pattern)
        self.assertGreater(match, 0)


class TestVerifyLoop(unittest.TestCase):

    def setUp(self):
        self.mock_dex = MagicMock()
        self.mock_filter = MagicMock()
        self.mock_filter.record_signal_result = MagicMock()
        os.environ["DATA_FILE"] = "./test_verify_data.json"
        from config import config
        config.data_file = "./test_verify_data.json"
        self.loop = VerifyLoop(self.mock_dex, self.mock_filter)

    def tearDown(self):
        for f in ["./test_verify_data.json", "test_verify_data.json", "bot_data.json"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_initial_state(self):
        self.assertEqual(len(self.loop.pending_verifications), 0)
        self.assertEqual(len(self.loop.completed), 0)

    def test_schedule_verification(self):
        self.loop.schedule_verification("addr", "SYM", 1000.0, 0.001, 0.5, 0.7)
        self.assertEqual(len(self.loop.pending_verifications), 1)

    def test_get_pending(self):
        self.loop.schedule_verification("a1", "S1", 1000.0, 0.001)
        self.loop.schedule_verification("a2", "S2", 2000.0, 0.002)
        pending = self.loop.get_pending()
        self.assertEqual(len(pending), 2)

    def test_get_completed_empty(self):
        completed = self.loop.get_completed()
        self.assertEqual(len(completed), 0)

    def test_stats_initial(self):
        stats = self.loop.get_stats()
        self.assertEqual(stats["total_verified"], 0)
        self.assertEqual(stats["win_rate"], 0)


if __name__ == "__main__":
    unittest.main()
