import unittest
import os
import json
from unittest.mock import AsyncMock, MagicMock
from backtest import BacktestEngine, REPORTS_DIR


class TestBacktest(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        self.mock_dex = MagicMock()
        self.mock_helius = MagicMock()
        self.engine = BacktestEngine(
            self.mock_session, self.mock_dex, self.mock_helius, send_msg_func=None
        )

    def test_identify_pump_positive(self):
        pair = {"priceChange": {"h1": 250, "h6": 100, "h24": 50}}
        is_pump, multi = self.engine.identify_pump(pair)
        self.assertTrue(is_pump)
        self.assertEqual(multi, 3.5)

    def test_identify_pump_negative(self):
        pair = {"priceChange": {"h1": 50, "h6": 30, "h24": 10}}
        is_pump, multi = self.engine.identify_pump(pair)
        self.assertFalse(is_pump)

    def test_identify_pump_handles_missing(self):
        pair = {}
        is_pump, multi = self.engine.identify_pump(pair)
        self.assertFalse(is_pump)

    def test_calculate_metrics_basic(self):
        results = [
            {"verdict": "TP", "actual_multiplier": 4.0, "actual_pump": True, "reason": "test", "age_seconds": 3600},
            {"verdict": "TP", "actual_multiplier": 3.5, "actual_pump": True, "reason": "test", "age_seconds": 7200},
            {"verdict": "FP", "actual_multiplier": 1.0, "actual_pump": False, "reason": "test", "age_seconds": 3600},
            {"verdict": "TN", "actual_multiplier": 0.5, "actual_pump": False, "reason": "test", "age_seconds": 3600},
            {"verdict": "FN", "actual_multiplier": 5.0, "actual_pump": True, "reason": "test", "age_seconds": 7200},
        ]
        metrics = self.engine.calculate_metrics(results)
        self.assertEqual(metrics["tp"], 2)
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["tn"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertEqual(metrics["actual_pumps"], 3)
        self.assertEqual(metrics["signals_sent"], 3)
        self.assertEqual(metrics["precision"], round(2/3*100, 1))
        self.assertEqual(metrics["recall"], round(2/3*100, 1))
        self.assertEqual(metrics["accuracy"], round(3/5*100, 1))
        self.assertEqual(metrics["avg_multiplier"], 3.75)

    def test_calculate_metrics_empty(self):
        metrics = self.engine.calculate_metrics([])
        self.assertEqual(metrics["total_tokens"], 0)
        self.assertEqual(metrics["precision"], 0)
        self.assertEqual(metrics["recall"], 0)

    def test_calculate_metrics_no_signals(self):
        results = [
            {"verdict": "TN", "actual_multiplier": 0.5, "actual_pump": False, "reason": "test", "age_seconds": 3600},
            {"verdict": "TN", "actual_multiplier": 0.3, "actual_pump": False, "reason": "test", "age_seconds": 3600},
        ]
        metrics = self.engine.calculate_metrics(results)
        self.assertEqual(metrics["signals_sent"], 0)
        self.assertEqual(metrics["precision"], 0)

    def test_format_telegram_report(self):
        metrics = {
            "total_tokens": 100,
            "actual_pumps": 5,
            "dumps": 95,
            "signals_sent": 6,
            "tp": 4, "fp": 2, "tn": 90, "fn": 1,
            "precision": 66.7,
            "recall": 80.0,
            "f1_score": 0.727,
            "accuracy": 94.0,
            "win_rate": 66.7,
            "avg_multiplier": 4.2,
            "hour_success_rate": {"14": 0.8, "18": 0.7, "22": 0.6},
        }
        text = self.engine._format_telegram_report(metrics, 30)
        self.assertIn("Backtest Report", text)
        self.assertIn("30 days", text)
        self.assertIn("66.7%", text)
        self.assertIn("14:00", text)
        self.assertIn("Verdict", text)

    def test_save_report_files(self):
        test_dir = "./test_backtest_reports"
        os.makedirs(test_dir, exist_ok=True)
        original_reports_dir = REPORTS_DIR
        import backtest
        backtest.REPORTS_DIR = test_dir

        try:
            metrics = {
                "total_tokens": 10, "actual_pumps": 2, "dumps": 8,
                "tp": 1, "fp": 1, "tn": 7, "fn": 1,
                "precision": 50.0, "recall": 50.0, "f1_score": 0.5,
                "accuracy": 80.0, "win_rate": 50.0, "avg_multiplier": 3.5,
                "hour_success_rate": {},
            }
            results = [{"verdict": "TP", "symbol": "TEST", "ai_score": 0.8, "actual_multiplier": 4.0}]

            json_path = self.engine._save_report_files(metrics, results, 30)
            self.assertTrue(os.path.exists(json_path))
            self.assertTrue(os.path.exists("backtest_summary.md"))

            with open(json_path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["period_days"], 30)
            self.assertIn("metrics", data)
        finally:
            backtest.REPORTS_DIR = original_reports_dir
            for f in os.listdir(test_dir):
                os.remove(os.path.join(test_dir, f))
            os.rmdir(test_dir)
            if os.path.exists("backtest_summary.md"):
                os.remove("backtest_summary.md")

    def test_cleanup_old_reports(self):
        test_dir = "./test_cleanup_reports"
        os.makedirs(test_dir, exist_ok=True)
        import backtest
        original_reports_dir = backtest.REPORTS_DIR
        backtest.REPORTS_DIR = test_dir

        try:
            for i in range(15):
                fname = f"backtest_2026010{i}_120000.json"
                with open(os.path.join(test_dir, fname), "w") as f:
                    f.write("{}")
                import time
                time.sleep(0.01)

            self.engine._cleanup_old_reports()
            remaining = len([f for f in os.listdir(test_dir) if f.endswith(".json")])
            self.assertLessEqual(remaining, 10)
        finally:
            backtest.REPORTS_DIR = original_reports_dir
            for f in os.listdir(test_dir):
                os.remove(os.path.join(test_dir, f))
            os.rmdir(test_dir)

    def test_evaluate_token_logic(self):
        token_info = {
            "address": "test_addr",
            "name": "TestToken",
            "symbol": "TEST",
            "pair": {
                "fdv": 100000,
                "liquidity": {"usd": 5000},
                "volume": {"h1": 1000, "m5": 200},
                "priceChange": {"h1": 350, "h6": 200, "h24": 100, "m5": 10},
                "txns": {"m5": {"buys": 20, "sells": 5}, "h1": {"buys": 100, "sells": 30}},
                "pairCreatedAt": None,
            },
        }
        import asyncio
        result = asyncio.run(self.engine.evaluate_token(token_info))
        self.assertIn("verdict", result)
        self.assertIn("ai_score", result)
        self.assertIn("actual_pump", result)
        self.assertTrue(result["actual_pump"])


if __name__ == "__main__":
    unittest.main()
