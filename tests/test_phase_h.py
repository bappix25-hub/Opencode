import unittest
import os
import subprocess
import tempfile
from unittest.mock import patch


class TestGithubSyncPAT(unittest.TestCase):

    def setUp(self):
        self._saved = {}
        for k in ["GITHUB_PAT", "GITHUB_USER", "GITHUB_REPO", "BOT_TOKEN",
                  "CHAT_ID", "HELIUS_API_KEY", "BOT_INSTANCE", "DATA_FILE"]:
            self._saved[k] = os.environ.get(k)
            os.environ.pop(k, None)
        os.environ["BOT_TOKEN"] = "test"
        os.environ["CHAT_ID"] = "5461546008"
        os.environ["HELIUS_API_KEY"] = "test"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _reload(self):
        import importlib
        import github_sync
        importlib.reload(github_sync)
        return github_sync

    def test_no_pat_uses_public_url(self):
        os.environ.pop("GITHUB_PAT", None)
        os.environ["GITHUB_USER"] = "alice"
        os.environ["GITHUB_REPO"] = "bot"
        gs = self._reload()
        self.assertEqual(gs.REMOTE_URL, "https://github.com/alice/bot.git")
        self.assertEqual(gs.BASE_REMOTE_URL, "https://github.com/alice/bot.git")
        self.assertNotIn("ghp_", gs.REMOTE_URL)

    def test_pat_injected_in_url(self):
        os.environ["GITHUB_PAT"] = "ghp_FAKE_TOKEN_xyz"
        os.environ["GITHUB_USER"] = "alice"
        os.environ["GITHUB_REPO"] = "bot"
        gs = self._reload()
        self.assertIn("ghp_FAKE_TOKEN_xyz", gs.REMOTE_URL)
        self.assertIn("alice:ghp_FAKE_TOKEN_xyz", gs.REMOTE_URL)
        self.assertTrue(gs.REMOTE_URL.startswith("https://"))

    def test_pat_with_default_user_repo(self):
        os.environ["GITHUB_PAT"] = "ghp_FAKE"
        gs = self._reload()
        self.assertIn("bappix25-hub", gs.REMOTE_URL)
        self.assertIn("Opencode", gs.REMOTE_URL)
        self.assertIn("ghp_FAKE", gs.REMOTE_URL)

    def test_empty_pat_treated_as_none(self):
        os.environ["GITHUB_PAT"] = ""
        gs = self._reload()
        self.assertNotIn("ghp_", gs.REMOTE_URL)
        self.assertEqual(gs.REMOTE_URL, "https://github.com/bappix25-hub/Opencode.git")

    def test_branch_default_main(self):
        os.environ.pop("BOT_INSTANCE", None)
        gs = self._reload()
        self.assertEqual(gs.GIT_BRANCH, "main")

    def test_branch_v2(self):
        os.environ["BOT_INSTANCE"] = "v2"
        gs = self._reload()
        self.assertEqual(gs.GIT_BRANCH, "v2-data")

    def test_sync_files_contains_data_file(self):
        gs = self._reload()
        data_file = gs.DATA_FILE
        self.assertIn(data_file, gs.SYNC_FILES)
        self.assertIn("meme_bot.py", gs.SYNC_FILES)


class TestStartShGitHubConfig(unittest.TestCase):

    def test_start_sh_pat_block(self):
        with open("/home/user/Opencode/start.sh") as f:
            content = f.read()
        self.assertIn("GITHUB_PAT", content)
        self.assertIn("git remote set-url", content)
        self.assertIn("REMOTE_URL=", content)

    def test_start_sh_no_pat_message(self):
        with open("/home/user/Opencode/start.sh") as f:
            content = f.read()
        self.assertIn("PAT configured", content)
        self.assertIn("public clone", content)


class TestEnvExampleGitHubFields(unittest.TestCase):

    def test_env_example_has_pat(self):
        with open("/home/user/Opencode/.env.example") as f:
            content = f.read()
        self.assertIn("GITHUB_PAT=", content)
        self.assertIn("GITHUB_USER=", content)
        self.assertIn("GITHUB_REPO=", content)

    def test_env_example_pat_empty_default(self):
        with open("/home/user/Opencode/.env.example") as f:
            for line in f:
                if line.startswith("GITHUB_PAT="):
                    self.assertIn("GITHUB_PAT=\n", line)
                    return
        self.fail("GITHUB_PAT= line not found")


class TestSetupEnvShGitHubPrompt(unittest.TestCase):

    def test_setup_env_prompts_for_pat(self):
        with open("/home/user/Opencode/setup_env.sh") as f:
            content = f.read()
        self.assertIn("GitHub PAT", content)
        self.assertIn("GITHUB_PAT=$GITHUB_PAT", content)
        self.assertIn("GITHUB_USER=$GITHUB_USER", content)
        self.assertIn("GITHUB_REPO=$GITHUB_REPO", content)

    def test_setup_env_pat_optional(self):
        with open("/home/user/Opencode/setup_env.sh") as f:
            content = f.read()
        self.assertIn("optional", content.lower())


if __name__ == "__main__":
    unittest.main()
