import unittest
from learner import (
    load_data, save_data, is_duplicate, learn_pump, learn_dump,
    score_coin, score_launch, verify_pump, get_launch_age,
    extract_pattern, _hash_address
)

class TestLearner(unittest.TestCase):

    def setUp(self):
        import os
        os.environ["DATA_FILE"] = "./test_bot_data.json"
        from config import config
        self.original_data_file = config.data_file
        config.data_file = "./test_bot_data.json"

        if os.path.exists("./test_bot_data.json"):
            os.remove("./test_bot_data.json")

    def tearDown(self):
        import os
        if os.path.exists("./test_bot_data.json"):
            os.remove("./test_bot_data.json")

    def test_hash_address(self):
        h1 = _hash_address("TokenABC123")
        h2 = _hash_address("tokenabc123")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 32)

    def test_verify_pump(self):
        pair = {
            "priceChange": {"h1": 250, "h6": 100, "h24": 50}
        }
        verified, multi = verify_pump(pair, 3.0)
        self.assertTrue(verified)
        self.assertEqual(multi, 3.5)

        pair_low = {"priceChange": {"h1": 50, "h6": 30, "h24": 10}}
        verified, multi = verify_pump(pair_low, 3.0)
        self.assertFalse(verified)

    def test_extract_pattern(self):
        pair = {
            "fdv": 100000,
            "liquidity": {"usd": 5000},
            "volume": {"h1": 1000, "m5": 200},
            "priceChange": {"m5": 10, "h1": 50},
            "txns": {
                "m5": {"buys": 20, "sells": 5},
                "h1": {"buys": 100, "sells": 30}
            }
        }
        pattern = extract_pattern(pair, age_seconds=300)
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["mcap"], 100000)
        self.assertEqual(pattern["liquidity"], 5000)
        self.assertEqual(pattern["buys_m5"], 20)
        self.assertEqual(pattern["sells_m5"], 5)

    def test_learn_pump_and_duplicate_check(self):
        pair = {
            "fdv": 100000,
            "liquidity": {"usd": 5000},
            "volume": {"h1": 1000, "m5": 200},
            "priceChange": {"h1": 300, "h6": 200, "h24": 100, "m5": 10},
            "txns": {
                "m5": {"buys": 20, "sells": 5},
                "h1": {"buys": 100, "sells": 30}
            },
            "pairCreatedAt": None
        }
        coin = {"name": "TestCoin", "symbol": "TEST"}

        ok, msg = learn_pump(coin, pair, 3.0, "test_addr_1", manual=True)
        self.assertTrue(ok)

        self.assertTrue(is_duplicate("test_addr_1"))

        ok2, msg2 = learn_pump(coin, pair, 3.0, "test_addr_1", manual=True)
        self.assertFalse(ok2)
        self.assertIn("ডুপ্লিকেট", msg2)

    def test_score_coin_cold_start(self):
        pair = {
            "fdv": 50000,
            "liquidity": {"usd": 6000},
            "volume": {"m5": 500},
            "priceChange": {"m5": 10, "h1": 5},
            "txns": {
                "m5": {"buys": 30, "sells": 10}
            }
        }
        coin = {"name": "Test", "symbol": "TST"}
        score, reason = score_coin(pair, coin, age_seconds=300)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_score_launch_cold_start(self):
        launch_data = {
            "buy_count": 15,
            "unique_wallets": 8,
            "volume": 2.5,
            "buy_sell_ratio": 3.0
        }
        score, reason = score_launch(launch_data)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_learn_dump(self):
        pair = {
            "fdv": 50000,
            "liquidity": {"usd": 2000},
            "volume": {"h1": 100},
            "priceChange": {"h1": -50},
            "txns": {"h1": {"buys": 5, "sells": 50}}
        }
        coin = {"name": "DumpCoin", "symbol": "DUMP"}
        ok, msg = learn_dump(coin, pair, "dump_addr_1", manual=True)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
