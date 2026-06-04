import unittest
import os
import logging
import tempfile
import subprocess
from datetime import datetime, timezone
from utils import setup_logging, get_launch_age, verify_pump, format_number, gmgn_link


class TestLoggingRotation(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, "test_bot.log")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)
        os.environ.pop("LOG_FILE", None)

    def test_setup_logging_console_only(self):
        os.environ.pop("LOG_FILE", None)
        logger = setup_logging("test_console")
        self.assertEqual(len(logger.handlers), 1)

    def test_setup_logging_with_file(self):
        os.environ["LOG_FILE"] = self.log_path
        logger = setup_logging("test_file")
        self.assertGreaterEqual(len(logger.handlers), 3)
        logger.info("test message")
        self.assertTrue(os.path.exists(self.log_path))
        self.assertTrue(os.path.exists(self.log_path + ".size"))

    def test_setup_logging_idempotent(self):
        os.environ["LOG_FILE"] = self.log_path
        l1 = setup_logging("test_idem")
        h1 = len(l1.handlers)
        l2 = setup_logging("test_idem")
        self.assertEqual(len(l2.handlers), h1)

    def test_logging_writes_to_file(self):
        os.environ["LOG_FILE"] = self.log_path
        logger = setup_logging("test_write")
        logger.info("hello world")
        with open(self.log_path, "r") as f:
            content = f.read()
        self.assertIn("hello world", content)
        self.assertIn("test_write", content)

    def test_logging_size_cap_handler(self):
        os.environ["LOG_FILE"] = self.log_path
        logger = setup_logging("test_cap")
        size_handlers = [h for h in logger.handlers if "size" in str(h)]
        self.assertGreater(len(size_handlers), 0)


class TestStartScript(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.env_path = os.path.join(self.tmpdir, ".env")
        with open(self.env_path, "w") as f:
            f.write("BOT_TOKEN=test_token\nDATA_FILE=./test_data.json\nLOG_FILE=./test.log\n")
        self.env_v2_path = os.path.join(self.tmpdir, ".env.v2")
        with open(self.env_v2_path, "w") as f:
            f.write("BOT_TOKEN=v2_token\nDATA_FILE=./test_v2.json\nLOG_FILE=./test_v2.log\n")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            try:
                os.remove(os.path.join(self.tmpdir, f))
            except Exception:
                pass
        os.rmdir(self.tmpdir)

    def test_start_sh_exists(self):
        result = subprocess.run(
            ["test", "-x", "/home/user/Opencode/start.sh"],
            capture_output=True
        )
        self.assertEqual(result.returncode, 0, "start.sh not executable")

    def test_start_sh_uses_dot_env(self):
        result = subprocess.run(
            ["bash", "-c", "grep -q 'ENV_FILE=\"\\.env\"' /home/user/Opencode/start.sh"],
            capture_output=True
        )
        self.assertEqual(result.returncode, 0)

    def test_start_sh_v2_arg(self):
        result = subprocess.run(
            ["bash", "-c", "grep -q 'v2' /home/user/Opencode/start.sh"],
            capture_output=True
        )
        self.assertEqual(result.returncode, 0)

    def test_start_sh_log_redirect(self):
        result = subprocess.run(
            ["bash", "-c", "grep -q 'tee.*LOG_FILE' /home/user/Opencode/start.sh || grep -q '>>.*LOG_FILE' /home/user/Opencode/start.sh"],
            capture_output=True
        )
        self.assertEqual(result.returncode, 0)

    def test_start_sh_env_v2_load(self):
        result = subprocess.run(
            ["bash", "-c", f"cd {self.tmpdir} && DATA_FILE='${{DATA_FILE:-default}}' bash -c 'set -a; source {self.env_path}; set +a; echo $DATA_FILE'"],
            capture_output=True, text=True
        )
        self.assertIn("test_data.json", result.stdout)


class TestGitignoreSecurity(unittest.TestCase):

    def test_env_files_ignored(self):
        with open("/home/user/Opencode/.gitignore") as f:
            content = f.read()
        self.assertIn(".env", content)
        self.assertIn(".env.v2", content)

    def test_data_files_ignored(self):
        with open("/home/user/Opencode/.gitignore") as f:
            content = f.read()
        self.assertIn("bot_data.json", content)
        self.assertIn("bot_data_v2.json", content)

    def test_log_files_ignored(self):
        with open("/home/user/Opencode/.gitignore") as f:
            content = f.read()
        self.assertIn("*.log", content)

    def test_secret_files_ignored(self):
        with open("/home/user/Opencode/.gitignore") as f:
            content = f.read()
        self.assertIn("golden_patterns.json", content)
        self.assertIn("blacklist_patterns.json", content)


class TestGitHubSyncBranch(unittest.TestCase):

    def test_branch_default_main(self):
        os.environ.pop("BOT_INSTANCE", None)
        os.environ.pop("GIT_BRANCH", None)
        import importlib
        import github_sync
        importlib.reload(github_sync)
        self.assertEqual(github_sync.GIT_BRANCH, "main")
        self.assertEqual(github_sync.BOT_INSTANCE, "main")

    def test_branch_v2(self):
        os.environ["BOT_INSTANCE"] = "v2"
        import importlib
        import github_sync
        importlib.reload(github_sync)
        self.assertEqual(github_sync.BOT_INSTANCE, "v2")
        self.assertEqual(github_sync.GIT_BRANCH, "v2-data")

    def test_sync_files_includes_new(self):
        os.environ["BOT_INSTANCE"] = "main"
        import importlib
        import github_sync
        importlib.reload(github_sync)
        self.assertIn("signal_filter.py", github_sync.SYNC_FILES)
        self.assertIn("verify_loop.py", github_sync.SYNC_FILES)
        self.assertIn("backtest.py", github_sync.SYNC_FILES)


if __name__ == "__main__":
    unittest.main()
