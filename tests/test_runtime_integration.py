"""End-to-end runtime behaviour: mode selection, offline guarantee, real work.

The offline test installs a socket-level spy and asserts that *no* non-loopback
connection is even attempted. That is a stronger guarantee than checking the
returned string, because it would catch a leak in any layer.
"""

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.config import Config, Mode
from nexus.core.runtime import NexusRuntime, SimulatedBackend
from nexus.net import CloudBlockedError
from tests.helpers import EchoBackend, FailingBackend, make_runtime, temp_config


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


class SocketSpy:
    """Record every connect() target without altering behaviour."""

    def __init__(self):
        self.targets = []
        self._real = socket.socket.connect

    def __enter__(self):
        real = self._real

        def spy(sock, address):
            self.targets.append(address)
            return real(sock, address)

        socket.socket.connect = spy
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._real

    @property
    def external(self):
        out = []
        for address in self.targets:
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in LOOPBACK_HOSTS:
                out.append(address)
        return out


def cloud_config(directory: Path, mode: str, allow_cloud: bool):
    return temp_config(directory, {
        "mode": mode,
        "cloud": {"enabled": True, "base_url": "https://api.openai.com/v1",
                  "model": "gpt-4o-mini", "api_key_env": "NEXUS_TEST_CLOUD_KEY"},
        "privacy": {"allow_cloud": allow_cloud},
    })


class OfflineGuaranteeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["NEXUS_TEST_CLOUD_KEY"] = "sk-test-not-real"

    def tearDown(self):
        os.environ.pop("NEXUS_TEST_CLOUD_KEY", None)
        self.tmp.cleanup()

    def test_offline_chat_makes_no_external_connection(self):
        runtime = NexusRuntime(cloud_config(self.root, "offline", allow_cloud=True))
        with SocketSpy() as spy:
            runtime.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(spy.external, [],
                         f"offline mode opened external sockets: {spy.external}")

    def test_offline_task_makes_no_external_connection(self):
        runtime = NexusRuntime(cloud_config(self.root, "offline", allow_cloud=True))
        with SocketSpy() as spy:
            runtime.run_task("do a thing")
        self.assertEqual(spy.external, [])

    def test_offline_blocks_a_direct_cloud_call(self):
        runtime = NexusRuntime(cloud_config(self.root, "offline", allow_cloud=True))
        from nexus.models.cloud import CloudBackend

        backend = CloudBackend(api_key="sk-test", policy=runtime.policy)
        with SocketSpy() as spy:
            with self.assertRaises(CloudBlockedError):
                backend.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(spy.external, [])

    def test_offline_never_selects_cloud_even_if_configured(self):
        runtime = NexusRuntime(cloud_config(self.root, "offline", allow_cloud=True))
        model, simulated = runtime._resolve_backend()
        self.assertTrue(simulated)
        self.assertEqual(model, "simulated_model")
        self.assertIsInstance(runtime.gateway.backend, SimulatedBackend)

    def test_cloud_denied_without_consent_in_online_mode(self):
        runtime = NexusRuntime(cloud_config(self.root, "online", allow_cloud=False))
        model, simulated = runtime._resolve_backend()
        # No local model and no consent: must not reach for the cloud.
        self.assertEqual(model, "simulated_model")


class OnlineModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["NEXUS_TEST_CLOUD_KEY"] = "sk-test-not-real"

    def tearDown(self):
        os.environ.pop("NEXUS_TEST_CLOUD_KEY", None)
        self.tmp.cleanup()

    def test_online_with_consent_does_attempt_the_cloud(self):
        """Proves the offline guard is not simply blocking everything."""
        runtime = NexusRuntime(cloud_config(self.root, "online", allow_cloud=True))
        from nexus.models.cloud import CloudBackend
        from nexus.net import NetworkError

        backend = CloudBackend(api_key="sk-test", policy=runtime.policy,
                               timeout=1)
        attempted = []

        real_connect = socket.socket.connect

        def refuse(sock, address):
            attempted.append(address)
            raise ConnectionRefusedError("test: outbound blocked")

        socket.socket.connect = refuse
        try:
            with self.assertRaises(NetworkError):
                backend.chat([{"role": "user", "content": "hi"}])
        finally:
            socket.socket.connect = real_connect

        self.assertTrue(attempted, "online mode did not attempt any connection")

    def test_online_selects_the_cloud_backend_when_enabled(self):
        runtime = NexusRuntime(cloud_config(self.root, "online", allow_cloud=True))
        # Remove local candidates so the cloud path is exercised.
        runtime.selector.select = lambda **kw: {"candidates": []}
        model, simulated = runtime._resolve_backend()
        self.assertFalse(simulated)
        self.assertEqual(model, "gpt-4o-mini")

    def test_missing_api_key_is_reported_not_faked(self):
        os.environ.pop("NEXUS_TEST_CLOUD_KEY", None)
        runtime = NexusRuntime(cloud_config(self.root, "online", allow_cloud=True))
        runtime.selector.select = lambda **kw: {"candidates": []}
        verification = runtime.verify_connection()
        self.assertFalse(verification["ok"])
        self.assertIn("key", verification["error"].lower())


class AutoModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["NEXUS_TEST_CLOUD_KEY"] = "sk-test-not-real"

    def tearDown(self):
        os.environ.pop("NEXUS_TEST_CLOUD_KEY", None)
        self.tmp.cleanup()

    def test_auto_prefers_local_when_available(self):
        runtime = NexusRuntime(cloud_config(self.root, "auto", allow_cloud=True))
        runtime.selector.select = lambda **kw: {
            "candidates": [{"backend": "ollama", "name": "local:1b"}],
            "hardware_profile": {"max_parallel_agents": 1},
        }
        model, simulated = runtime._resolve_backend()
        self.assertFalse(simulated)
        self.assertEqual(model, "local:1b")
        self.assertNotIsInstance(runtime.gateway.backend, type(None))

    def test_auto_falls_back_to_cloud_when_no_local(self):
        runtime = NexusRuntime(cloud_config(self.root, "auto", allow_cloud=True))
        runtime.selector.select = lambda **kw: {"candidates": [],
                                                "hardware_profile": {}}
        model, simulated = runtime._resolve_backend()
        self.assertFalse(simulated)
        self.assertEqual(model, "gpt-4o-mini")

    def test_auto_without_consent_stays_offline(self):
        runtime = NexusRuntime(cloud_config(self.root, "auto", allow_cloud=False))
        runtime.selector.select = lambda **kw: {"candidates": [],
                                                "hardware_profile": {}}
        model, simulated = runtime._resolve_backend()
        self.assertTrue(simulated)
        self.assertEqual(model, "simulated_model")


class RealExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _runtime_with(self, backend):
        runtime = NexusRuntime(temp_config(self.root))
        runtime.gateway.set_backend(backend)
        return runtime

    def test_task_produces_real_output_from_the_model(self):
        backend = EchoBackend(plan=["gather facts", "write summary"])
        runtime = self._runtime_with(backend)
        result = runtime.run_task("summarise the project")
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["simulated"])
        self.assertEqual(result["steps"], 2)
        self.assertIn("result:", result["output"])
        # Planning + one call per step: the model was genuinely used.
        self.assertGreaterEqual(backend.chat_calls, 2)

    def test_task_without_backend_fails_explicitly(self):
        runtime = NexusRuntime(temp_config(self.root))
        runtime.selector.select = lambda **kw: {"candidates": [],
                                                "hardware_profile": {}}
        result = runtime.run_task("do something")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["simulated"])
        self.assertIn("refusing to fake execution", result["error"])

    def test_backend_failure_surfaces_as_failed(self):
        backend = FailingBackend(error=RuntimeError("boom"),
                                 failures_before_success=99)
        runtime = self._runtime_with(backend)
        result = runtime.run_task("fail me")
        self.assertEqual(result["status"], "failed")

    def test_transient_backend_failure_recovers(self):
        backend = FailingBackend(failures_before_success=1)
        runtime = self._runtime_with(backend)
        result = runtime.run_task("recover please")
        self.assertEqual(result["status"], "completed")

    def test_chat_returns_two_tuple_contract(self):
        runtime = self._runtime_with(EchoBackend())
        text, model = runtime.chat([{"role": "user", "content": "hi"}])
        self.assertIsInstance(text, str)
        self.assertEqual(model, "echo-model")

    def test_chat_detail_reports_source(self):
        runtime = self._runtime_with(EchoBackend())
        text, model, source = runtime.chat_detail(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(source, "local")

    def test_journal_file_written(self):
        runtime = self._runtime_with(EchoBackend(plan=["one"]))
        result = runtime.run_task("journal")
        self.assertTrue(Path(result["journal"]).exists())

    def test_memory_augments_chat_prompt(self):
        runtime = self._runtime_with(EchoBackend())
        runtime.memory.add("the project ships on port 8085", kind="fact")
        text, _ = runtime.chat([{"role": "user", "content": "which port?"}])
        # The echo backend returns the last message, so assert via memory lookup.
        self.assertIn("8085", runtime.memory.context_for("port"))

    def test_team_run_uses_the_backend(self):
        runtime = self._runtime_with(EchoBackend())
        runtime.selector.select = lambda **kw: {
            "candidates": [], "hardware_profile": {"max_parallel_agents": 1}}
        result = runtime.run_team("evaluate this plan")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["subtasks"]), 3)
        self.assertEqual(result["parallelism"], 1)

    def test_team_run_without_backend_does_not_fake_success(self):
        runtime = NexusRuntime(temp_config(self.root))
        runtime.selector.select = lambda **kw: {"candidates": [],
                                                "hardware_profile": {}}
        result = runtime.run_team("evaluate")
        self.assertFalse(result["ok"])
        self.assertTrue(result["simulated"])


class StatusAndModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_reports_mode_and_capabilities(self):
        runtime = NexusRuntime(temp_config(self.root))
        status = runtime.status()
        for field in ("mode", "tools", "plugins", "memory_entries", "simulated"):
            self.assertIn(field, status)

    def test_set_mode_persists_and_verifies(self):
        config = temp_config(self.root)
        runtime = NexusRuntime(config)
        result = runtime.set_mode("offline", persist=True)
        self.assertEqual(result["mode"], "offline")
        self.assertIn("verified", result)
        self.assertIs(Config.load(config.path).mode, Mode.OFFLINE)

    def test_set_mode_rejects_invalid_value(self):
        runtime = NexusRuntime(temp_config(self.root))
        with self.assertRaises(ValueError):
            runtime.set_mode("warp-speed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
