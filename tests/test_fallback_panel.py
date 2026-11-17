"""Tests for AUTO-mode cloud fallback and the control-panel endpoints."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nexus.models.registry as registry
from nexus.config import Config
from nexus.core.runtime import NexusRuntime


class FakeCloud:
    model = "cloud-model"

    def __init__(self, fail=False):
        self.fail = fail

    def chat_full(self, payload, tools=None):
        if self.fail:
            raise ConnectionError("cloud down")
        return {"role": "assistant", "content": "CLOUD"}

    def probe(self):
        return {"available": not self.fail}


class DeadLocal:
    model = "dead"

    def chat_full(self, payload, tools=None):
        raise ConnectionError("local down")

    def chat(self, payload):
        raise ConnectionError("local down")

    def generate(self, prompt):
        raise ConnectionError("local down")


def build(mode, allow_cloud, cloud_fail=False, enabled=True):
    cfg = Config(data={
        "mode": mode,
        "cloud": {"enabled": enabled},
        "privacy": {"allow_cloud": allow_cloud},
    })
    runtime = NexusRuntime(config=cfg)
    runtime.gateway.set_backend(DeadLocal())
    runtime._cloud_backend = lambda: FakeCloud(fail=cloud_fail)
    return runtime


class CloudFallbackTest(unittest.TestCase):
    def setUp(self):
        # Discovery must fail so the local path is genuinely unusable.
        self._ollama, self._llamacpp = registry.OLLAMA_HOST, registry.LLAMACPP_HOST
        registry.OLLAMA_HOST = "http://127.0.0.1:9"
        registry.LLAMACPP_HOST = "http://127.0.0.1:9"

    def tearDown(self):
        registry.OLLAMA_HOST, registry.LLAMACPP_HOST = self._ollama, self._llamacpp

    def test_auto_with_authorization_falls_back_to_cloud(self):
        runtime = build("auto", True)
        message, model, source = runtime.chat_message(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(source, "cloud")
        self.assertEqual(model, "cloud-model")
        self.assertEqual(message["content"], "CLOUD")

    def test_auto_without_authorization_never_reaches_cloud(self):
        runtime = build("auto", False)
        _, _, source = runtime.chat_message([{"role": "user", "content": "hi"}])
        self.assertEqual(source, "simulated")

    def test_offline_never_reaches_cloud_even_when_authorized(self):
        runtime = build("offline", True)
        _, _, source = runtime.chat_message([{"role": "user", "content": "hi"}])
        self.assertEqual(source, "simulated")

    def test_auto_does_not_fall_back_when_cloud_disabled(self):
        runtime = build("auto", True, enabled=False)
        _, _, source = runtime.chat_message([{"role": "user", "content": "hi"}])
        self.assertEqual(source, "simulated")

    def test_chat_detail_also_falls_back_to_cloud(self):
        """chat_detail() backs the /v1/chat path; it needs the same fallback."""
        runtime = build("auto", True)
        text, model, source = runtime.chat_detail(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(source, "cloud")
        self.assertEqual(model, "cloud-model")
        self.assertEqual(text, "CLOUD")

    def test_chat_detail_keeps_offline_guarantee(self):
        runtime = build("offline", True)
        _, _, source = runtime.chat_detail([{"role": "user", "content": "hi"}])
        self.assertEqual(source, "simulated")

    def test_failed_cloud_fallback_degrades_to_simulation(self):
        runtime = build("auto", True, cloud_fail=True)
        _, _, source = runtime.chat_message([{"role": "user", "content": "hi"}])
        self.assertEqual(source, "simulated")


class CloudKeyStorageTest(unittest.TestCase):
    def test_key_is_read_from_dedicated_file_not_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp) / "config.json")
            cfg.set("cloud.api_key_env", "NEXUS_TEST_UNSET_KEY_VAR")
            key_file = cfg.path.parent / "cloud.key"
            key_file.write_text("file-secret", "utf-8")
            self.assertEqual(cfg.cloud_api_key(), "file-secret")

            cfg.save()
            self.assertNotIn("file-secret", cfg.path.read_text("utf-8"))

    def test_env_var_wins_over_file(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp) / "config.json")
            cfg.set("cloud.api_key_env", "NEXUS_TEST_KEY_VAR")
            (cfg.path.parent / "cloud.key").write_text("from-file", "utf-8")
            os.environ["NEXUS_TEST_KEY_VAR"] = "from-env"
            try:
                self.assertEqual(cfg.cloud_api_key(), "from-env")
            finally:
                del os.environ["NEXUS_TEST_KEY_VAR"]


if __name__ == "__main__":
    unittest.main()
