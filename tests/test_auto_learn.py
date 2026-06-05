import unittest
import os
import importlib


class TestAutoLearnFromTracking(unittest.TestCase):

    def setUp(self):
        self._test_data = "./test_auto_learn_data.json"
        for f in [
            self._test_data,
            "./test_bot_data.json",
            "./bot_data.json",
            "./test_learner_data.json",
        ]:
            if os.path.exists(f):
                os.remove(f)

        self._saved_env = {}
        for k in ["DATA_FILE"]:
            self._saved_env[k] = os.environ.get(k)
        os.environ["DATA_FILE"] = self._test_data

        import config as _config
        self._config = _config
        self._config.config.data_file = self._test_data

        import learner
        learner.DATA_FILE = self._test_data
        importlib.reload(learner)
        self.learner = learner

    def tearDown(self):
        for f in [
            self._test_data,
            "./test_bot_data.json",
            "./bot_data.json",
            "./test_learner_data.json",
        ]:
            if os.path.exists(f):
                os.remove(f)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_pump_above_threshold_adds_pump_pattern(self):
        learned, kind, msg = self.learner.auto_learn_from_tracking(
            address="SoMePumpAddr111111111111111111111111111111",
            symbol="PUMP",
            name="Pump Coin",
            launch_dict={"buy_count": 50, "sell_count": 5, "unique_wallets": 40, "volume": 1000.0},
            current_price=4.0,
            initial_price=1.0,
            holders=10,
            lp_locked=1.0,
            age_seconds=900,
            pump_threshold=3.0,
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "pump")
        data = self.learner.load_data()
        self.assertEqual(len(data["pump_patterns"]), 1)
        self.assertEqual(data["pump_patterns"][0]["final_multiplier"], 4.0)
        self.assertEqual(data["pump_patterns"][0]["source"], "auto_track")

    def test_dump_below_threshold_adds_dump_pattern(self):
        learned, kind, msg = self.learner.auto_learn_from_tracking(
            address="SoMeDumpAddr2222222222222222222222222222",
            symbol="DUMP",
            name="Dump Coin",
            launch_dict={"buy_count": 5, "sell_count": 50, "unique_wallets": 10, "volume": 500.0},
            current_price=0.4,
            initial_price=1.0,
            holders=2,
            age_seconds=900,
            pump_threshold=3.0,
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "dump")
        data = self.learner.load_data()
        self.assertEqual(len(data["dump_patterns"]), 1)
        self.assertEqual(len(data["pump_patterns"]), 0)

    def test_too_early_no_dump_learn(self):
        learned, kind, msg = self.learner.auto_learn_from_tracking(
            address="SoMeEarlyAddr3333333333333333333333333333",
            symbol="EARLY",
            name="Early Coin",
            launch_dict={"buy_count": 0, "sell_count": 0, "unique_wallets": 0, "volume": 0.0},
            current_price=0.5,
            initial_price=1.0,
            holders=1,
            age_seconds=30,
            pump_threshold=3.0,
        )
        self.assertFalse(learned)
        self.assertEqual(kind, "skip")
        data = self.learner.load_data()
        self.assertEqual(len(data["dump_patterns"]), 0)

    def test_duplicate_address_skipped(self):
        addr = "SoMeDupAddr444444444444444444444444444444"
        data = self.learner.load_data()
        data["trained_addresses"].append(self.learner._hash_address(addr))
        self.learner.save_data(data)

        learned, kind, msg = self.learner.auto_learn_from_tracking(
            address=addr,
            symbol="DUP",
            name="Dup Coin",
            launch_dict={"buy_count": 10, "sell_count": 1, "unique_wallets": 8, "volume": 200.0},
            current_price=5.0,
            initial_price=1.0,
            holders=5,
            age_seconds=900,
            pump_threshold=3.0,
        )
        self.assertFalse(learned)
        self.assertEqual(kind, "skip")
        self.assertIn("duplicate", msg)

    def test_invalid_price_skipped(self):
        learned, kind, msg = self.learner.auto_learn_from_tracking(
            address="SoMeZeroAddr5555555555555555555555555555",
            symbol="ZERO",
            name="Zero",
            launch_dict={"buy_count": 0, "sell_count": 0, "unique_wallets": 0, "volume": 0.0},
            current_price=0.0,
            initial_price=0.0,
            holders=0,
            age_seconds=900,
            pump_threshold=3.0,
        )
        self.assertFalse(learned)
        self.assertEqual(kind, "skip")

    def test_low_holder_token_learns_pump(self):
        learned, kind, _ = self.learner.auto_learn_from_tracking(
            address="SoMeLowHolder6666666666666666666666666666",
            symbol="LOW",
            name="Low Holder Coin",
            launch_dict={"buy_count": 3, "sell_count": 0, "unique_wallets": 3, "volume": 50.0},
            current_price=3.5,
            initial_price=1.0,
            holders=1,
            age_seconds=600,
            pump_threshold=3.0,
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "pump")
        data = self.learner.load_data()
        self.assertEqual(len(data["pump_patterns"]), 1)
        self.assertEqual(data["pump_patterns"][0]["holders"], 1)

    def test_unique_wallets_as_set(self):
        learned, kind, _ = self.learner.auto_learn_from_tracking(
            address="SoMeSetAddr77777777777777777777777777777",
            symbol="SET",
            name="Set Coin",
            launch_dict={"buy_count": 4, "sell_count": 1, "unique_wallets": {1, 2, 3, 4}, "volume": 80.0},
            current_price=3.2,
            initial_price=1.0,
            holders=4,
            age_seconds=800,
            pump_threshold=3.0,
        )
        self.assertTrue(learned)
        self.assertEqual(kind, "pump")


if __name__ == "__main__":
    unittest.main()
