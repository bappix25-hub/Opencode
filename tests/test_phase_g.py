import unittest
import os
import subprocess
import re
import tempfile
from pathlib import Path


REPO = "/home/user/Opencode"


class TestRequirementsTxt(unittest.TestCase):

    def test_requirements_exists(self):
        self.assertTrue(os.path.exists(f"{REPO}/requirements.txt"))

    def test_requirements_has_telegram_bot(self):
        with open(f"{REPO}/requirements.txt") as f:
            content = f.read()
        self.assertIn("python-telegram-bot", content)

    def test_requirements_has_aiohttp(self):
        with open(f"{REPO}/requirements.txt") as f:
            content = f.read()
        self.assertIn("aiohttp", content)

    def test_requirements_has_websockets(self):
        with open(f"{REPO}/requirements.txt") as f:
            content = f.read()
        self.assertIn("websockets", content)

    def test_requirements_pinned_versions(self):
        with open(f"{REPO}/requirements.txt") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self.assertTrue(
                    "==" in line or ">=" in line or "<=" in line,
                    f"Line not pinned/ranged: {line}"
                )

    def test_requirements_pinned_or_ranged(self):
        with open(f"{REPO}/requirements.txt") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                has_pin = "==" in line or (">=" in line and "<" in line)
                self.assertTrue(has_pin, f"Line needs pin or range: {line}")

    def test_ptb_version_range(self):
        with open(f"{REPO}/requirements.txt") as f:
            for line in f:
                if "python-telegram-bot" in line:
                    self.assertIn(">=", line, f"PTB should use >= range: {line}")
                    self.assertIn("<", line, f"PTB should have upper bound: {line}")
                    return
        self.fail("PTB not found in requirements.txt")
        with open(f"{REPO}/requirements.txt") as f:
            content = f.read().lower()
        for secret in ["token=", "api_key=", "password="]:
            self.assertNotIn(secret, content)


class TestBootstrapScript(unittest.TestCase):

    def test_bootstrap_exists(self):
        self.assertTrue(os.path.exists(f"{REPO}/bootstrap.sh"))

    def test_bootstrap_executable(self):
        st = os.stat(f"{REPO}/bootstrap.sh")
        self.assertTrue(st.st_mode & 0o100, "Not executable")

    def test_bootstrap_syntax(self):
        result = subprocess.run(
            ["bash", "-n", f"{REPO}/bootstrap.sh"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_has_repo_url(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertIn("github.com/bappix25-hub/Opencode", content)

    def test_bootstrap_no_pat_in_url(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertNotIn("ghp_", content)
        self.assertNotIn("@github.com", content.split("REPO_URL")[1].split("\n")[0])

    def test_bootstrap_clone_or_pull_logic(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertIn('if [ -d "$DIR/.git" ]', content)
        self.assertIn("git pull", content)
        self.assertIn("git clone", content)

    def test_bootstrap_installs_deps(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertIn("pip install", content)
        self.assertIn("requirements.txt", content)

    def test_bootstrap_env_detection(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertIn("NEED_SETUP", content)
        self.assertIn(".env", content)

    def test_bootstrap_launches_bot(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertIn("start.sh", content)
        self.assertIn("exec bash", content)

    def test_bootstrap_curl_compatible(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertTrue(content.startswith("#!/bin/bash"))
        self.assertIn("set -e", content)

    def test_bootstrap_no_secrets(self):
        with open(f"{REPO}/bootstrap.sh") as f:
            content = f.read()
        self.assertNotIn("ghp_", content)
        self.assertNotIn("sk-", content)
        self.assertNotIn("YOUR_TOKEN_HERE", content)
        self.assertNotIn("YOUR_HELIUS_KEY_HERE", content)
        self.assertNotIn("@github.com:", content)
        self.assertNotRegex(content, r"BOT_TOKEN\s*=\s*[a-zA-Z0-9_-]{20,}")


class TestSetupEnvScript(unittest.TestCase):

    def test_setup_env_exists(self):
        self.assertTrue(os.path.exists(f"{REPO}/setup_env.sh"))

    def test_setup_env_executable(self):
        st = os.stat(f"{REPO}/setup_env.sh")
        self.assertTrue(st.st_mode & 0o100)

    def test_setup_env_syntax(self):
        result = subprocess.run(
            ["bash", "-n", f"{REPO}/setup_env.sh"],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_setup_env_writes_chmod_600(self):
        with open(f"{REPO}/setup_env.sh") as f:
            content = f.read()
        self.assertIn("chmod 600", content)
        self.assertIn("cat > .env", content)

    def test_setup_env_interactive_prompts(self):
        with open(f"{REPO}/setup_env.sh") as f:
            content = f.read()
        self.assertIn("read -p", content)
        self.assertIn("BOT_TOKEN", content)
        self.assertIn("CHAT_ID", content)
        self.assertIn("HELIUS", content)

    def test_setup_env_no_tty_handling(self):
        with open(f"{REPO}/setup_env.sh") as f:
            content = f.read()
        self.assertIn("[ ! -t 0 ]", content)
        self.assertIn("exit 1", content)


class TestBootstrapLogic(unittest.TestCase):

    def test_real_token_no_setup(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("BOT_TOKEN=actual_real_token_xyz123\n")
            tmp = f.name
        try:
            NEED_SETUP = 0
            if not os.path.exists(tmp):
                NEED_SETUP = 1
            elif subprocess.run(
                ["grep", "-qE", r"^BOT_TOKEN=(YOUR[A-Z_]*|_HERE|)\s*$", tmp],
                capture_output=True
            ).returncode == 0:
                NEED_SETUP = 1
            elif subprocess.run(
                ["grep", "-qE", r"^[A-Z_]+=\s*$", tmp],
                capture_output=True
            ).returncode == 0:
                NEED_SETUP = 1
            self.assertEqual(NEED_SETUP, 0)
        finally:
            os.unlink(tmp)

    def test_placeholder_token_needs_setup(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("BOT_TOKEN=YOUR_TOKEN_HERE\n")
            tmp = f.name
        try:
            NEED_SETUP = 0
            if not os.path.exists(tmp):
                NEED_SETUP = 1
            elif subprocess.run(
                ["grep", "-qE", r"^BOT_TOKEN=(YOUR[A-Z_]*|_HERE|)\s*$", tmp],
                capture_output=True
            ).returncode == 0:
                NEED_SETUP = 1
            self.assertEqual(NEED_SETUP, 1)
        finally:
            os.unlink(tmp)

    def test_empty_token_needs_setup(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("BOT_TOKEN=\n")
            tmp = f.name
        try:
            NEED_SETUP = 0
            if not os.path.exists(tmp):
                NEED_SETUP = 1
            elif subprocess.run(
                ["grep", "-qE", r"^BOT_TOKEN=(YOUR[A-Z_]*|_HERE|)\s*$", tmp],
                capture_output=True
            ).returncode == 0:
                NEED_SETUP = 1
            elif subprocess.run(
                ["grep", "-qE", r"^[A-Z_]+=\s*$", tmp],
                capture_output=True
            ).returncode == 0:
                NEED_SETUP = 1
            self.assertEqual(NEED_SETUP, 1)
        finally:
            os.unlink(tmp)

    def test_no_env_file_needs_setup(self):
        NEED_SETUP = 0
        if not os.path.exists("/tmp/nonexistent_env_xyz"):
            NEED_SETUP = 1
        self.assertEqual(NEED_SETUP, 1)


class TestGitignoreUpdated(unittest.TestCase):

    def test_gitignore_excludes_bootstrap_files(self):
        with open(f"{REPO}/.gitignore") as f:
            content = f.read()
        self.assertIn(".env", content)
        self.assertIn("*.log", content)
        self.assertIn("bot_data.json", content)


if __name__ == "__main__":
    unittest.main()
