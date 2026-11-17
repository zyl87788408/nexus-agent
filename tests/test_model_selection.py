"""Client-selected model support: /v1/models listing and `model` honouring."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import server
from nexus.core.runtime import NexusRuntime, SimulatedBackend
from nexus.config import Config

OLLAMA_3B = {"backend": "ollama", "name": "qwen2.5:3b", "size_bytes": 2040109465,
             "parameters": "3B", "quantization": "Q4_K_M"}
OLLAMA_7B = {"backend": "ollama", "name": "qwen2.5:7b", "size_bytes": 5046586572,
             "parameters": "7B", "quantization": "Q4_K_M"}


class PickCandidateTest(unittest.TestCase):
    """`_pick` must honour only candidates that survived the budget filter."""

    def setUp(self):
        self.runtime = NexusRuntime(Config.load())
        self.candidates = [
            {"name": "qwen2.5:7b", "backend": "ollama"},
            {"name": "qwen2.5:3b", "backend": "ollama"},
        ]

    def test_returns_requested_model_when_affordable(self):
        picked = self.runtime._pick(self.candidates, "qwen2.5:3b")
        self.assertEqual(picked["name"], "qwen2.5:3b")

    def test_falls_back_to_largest_when_no_preference(self):
        picked = self.runtime._pick(self.candidates, None)
        self.assertEqual(picked["name"], "qwen2.5:7b")

    def test_unknown_model_falls_back_instead_of_failing(self):
        picked = self.runtime._pick(self.candidates, "does-not-exist")
        self.assertEqual(picked["name"], "qwen2.5:7b")

    def test_empty_preference_string_falls_back(self):
        picked = self.runtime._pick(self.candidates, "")
        self.assertEqual(picked["name"], "qwen2.5:7b")

    def test_unaffordable_model_is_not_resurrected(self):
        # 7b was filtered out by the budget upstream, so selecting it by name
        # must not bypass that decision.
        affordable = [{"name": "qwen2.5:3b", "backend": "ollama"}]
        picked = self.runtime._pick(affordable, "qwen2.5:7b")
        self.assertEqual(picked["name"], "qwen2.5:3b")


class ModelsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_lists_installed_models(self):
        with patch("server.discover_models", return_value=[OLLAMA_3B, OLLAMA_7B]):
            body = self.client.get("/v1/models").json()
        self.assertEqual(body["object"], "list")
        ids = [m["id"] for m in body["data"]]
        self.assertEqual(ids, ["qwen2.5:3b", "qwen2.5:7b"])
        self.assertEqual(body["data"][0]["owned_by"], "ollama")

    def test_falls_back_to_placeholder_when_none_installed(self):
        with patch("server.discover_models", return_value=[]):
            body = self.client.get("/v1/models").json()
        self.assertEqual([m["id"] for m in body["data"]], ["nexus-local"])

    def test_discovery_failure_does_not_break_endpoint(self):
        with patch("server.discover_models", side_effect=RuntimeError("offline")):
            response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m["id"] for m in response.json()["data"]], ["nexus-local"])


class RequestedModelIsUsedTest(unittest.TestCase):
    """End-to-end: the name the client sends must reach the chosen backend."""

    def setUp(self):
        self.client = TestClient(server.app)
        self._original = server.runtime.gateway.backend
        server.runtime.gateway.set_backend(SimulatedBackend())

    def tearDown(self):
        server.runtime.gateway.set_backend(self._original)

    def test_explicit_model_is_reported_back(self):
        seen = {}

        def fake_chat(self_, messages, *, tools=None, use_memory=True, model=None):
            seen["model"] = model
            return {"role": "assistant", "content": "ok"}, model or "auto", "local"

        with patch.object(NexusRuntime, "chat_message", fake_chat):
            response = self.client.post(
                "/v1/chat/completions",
                json={"model": "qwen2.5:3b",
                      "messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["model"], "qwen2.5:3b")
        self.assertEqual(response.json()["model"], "qwen2.5:3b")

    def test_default_model_value_is_forwarded(self):
        seen = {}

        def fake_chat(self_, messages, *, tools=None, use_memory=True, model=None):
            seen["model"] = model
            return {"role": "assistant", "content": "ok"}, "simulated_model", "simulated"

        with patch.object(NexusRuntime, "chat_message", fake_chat):
            self.client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(seen["model"], "nexus-local")


if __name__ == "__main__":
    unittest.main()
