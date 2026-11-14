"""Tests for configuration and operating-mode semantics."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.config import Config, Mode
from nexus.net import CloudBlockedError, NetworkPolicy, is_loopback


class ModeParseTest(unittest.TestCase):
    def test_accepts_all_three_modes_case_insensitively(self):
        for raw, expected in [("offline", Mode.OFFLINE), ("ONLINE", Mode.ONLINE),
                              ("Auto", Mode.AUTO)]:
            self.assertIs(Mode.parse(raw), expected)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            Mode.parse("turbo")


class LoopbackTest(unittest.TestCase):
    def test_local_targets_are_loopback(self):
        for url in ("http://127.0.0.1:11434/api/tags",
                    "http://localhost:8080/v1/models",
                    "http://[::1]:11434", "localhost"):
            self.assertTrue(is_loopback(url), url)

    def test_remote_targets_are_not_loopback(self):
        for url in ("https://api.openai.com/v1", "http://10.0.0.5:8080",
                    "https://example.com"):
            self.assertFalse(is_loopback(url), url)


class NetworkPolicyMatrixTest(unittest.TestCase):
    def test_offline_permits_local_only(self):
        policy = NetworkPolicy(Mode.OFFLINE, allow_cloud=True)
        self.assertTrue(policy.allows("http://127.0.0.1:11434/api/tags"))
        # Even with cloud consent recorded, offline mode must refuse.
        self.assertFalse(policy.allows("https://api.openai.com/v1"))

    def test_offline_raises_before_any_socket_opens(self):
        policy = NetworkPolicy(Mode.OFFLINE, allow_cloud=True)
        with self.assertRaises(CloudBlockedError):
            policy.check("https://api.openai.com/v1/chat/completions")

    def test_online_requires_explicit_consent(self):
        self.assertFalse(NetworkPolicy(Mode.ONLINE, False).allows(
            "https://api.openai.com/v1"))
        self.assertTrue(NetworkPolicy(Mode.ONLINE, True).allows(
            "https://api.openai.com/v1"))

    def test_auto_still_needs_consent_for_cloud(self):
        self.assertFalse(NetworkPolicy(Mode.AUTO, False).allows(
            "https://api.openai.com/v1"))
        self.assertTrue(NetworkPolicy(Mode.AUTO, True).allows(
            "https://api.openai.com/v1"))

    def test_unresolvable_host_fails_closed(self):
        # An unknown name must not be treated as local.
        self.assertFalse(is_loopback("http://this-host-does-not-exist.invalid"))


class ConfigPersistenceTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            cfg = Config(path)
            cfg.mode = Mode.OFFLINE
            cfg.set("cloud.model", "qwen2.5:3b")
            cfg.save()

            loaded = Config.load(path)
            self.assertIs(loaded.mode, Mode.OFFLINE)
            self.assertEqual(loaded.get("cloud.model"), "qwen2.5:3b")

    def test_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            Config(path).save()
            mode = oct(path.stat().st_mode)[-3:]
            self.assertEqual(mode, "600")

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text("{ this is not json", "utf-8")
            cfg = Config.load(path)
            self.assertIs(cfg.mode, Mode.AUTO)

    def test_default_bind_is_loopback(self):
        self.assertEqual(Config().get("server.host"), "127.0.0.1")

    def test_default_cors_is_not_wildcard(self):
        origins = Config().get("server.cors_origins")
        self.assertNotIn("*", origins)

    def test_env_override(self):
        old = os.environ.get("NEXUS_MODE")
        try:
            os.environ["NEXUS_MODE"] = "offline"
            self.assertIs(Config().mode, Mode.OFFLINE)
        finally:
            if old is None:
                os.environ.pop("NEXUS_MODE", None)
            else:
                os.environ["NEXUS_MODE"] = old

    def test_secret_values_are_not_stored(self):
        """Config stores an env var *name*, never the key itself."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            cfg = Config(path)
            cfg.save()
            text = path.read_text("utf-8")
            self.assertNotIn("sk-", text)
            self.assertIn("NEXUS_CLOUD_API_KEY", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
