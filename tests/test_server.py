import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import server
from nexus.core.runtime import SimulatedBackend


class ChatCompletionsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self._original = server.runtime.gateway.backend
        # Pin the backend so these protocol tests do not depend on (or pay for)
        # whatever real model happens to be installed on the host.
        server.runtime.gateway.set_backend(SimulatedBackend())

    def tearDown(self):
        server.runtime.gateway.set_backend(self._original)

    def test_returns_openai_shape(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "你好"}]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertTrue(body["id"].startswith("chatcmpl-"))
        self.assertIsInstance(body["created"], int)
        choice = body["choices"][0]
        self.assertEqual(choice["index"], 0)
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertIsInstance(choice["message"]["content"], str)
        self.assertEqual(choice["finish_reason"], "stop")
        usage = body["usage"]
        self.assertEqual(
            usage["total_tokens"], usage["prompt_tokens"] + usage["completion_tokens"]
        )

    def test_simulated_fallback_is_labelled(self):
        """Force the simulated path instead of relying on the host having no model.

        The contract is that a simulated answer is *labelled* as such, not that
        this machine happens to lack a model.
        """
        import server
        from nexus.core.runtime import SimulatedBackend

        original = server.runtime.gateway.backend
        server.runtime.gateway.set_backend(SimulatedBackend())
        try:
            body = self.client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "没有模型时也要能答"}]},
            ).json()
        finally:
            server.runtime.gateway.set_backend(original)

        self.assertEqual(body["model"], "simulated_model")
        self.assertTrue(body["nexus"]["simulated"])
        self.assertEqual(body["nexus"]["source"], "simulated")

    def test_empty_messages_rejected(self):
        response = self.client.post("/v1/chat/completions", json={"messages": []})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_multipart_content_is_flattened(self):
        body = self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "分段内容测试"}],
                    }
                ]
            },
        ).json()
        self.assertIn("分段内容测试", body["choices"][0]["message"]["content"])

    def test_streaming_sse_format(self):
        with self.client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "流式"}], "stream": True},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            lines = [
                line for line in response.iter_lines() if line.startswith("data: ")
            ]
        self.assertTrue(lines[0].startswith("data: {"))
        self.assertEqual(lines[-1], "data: [DONE]")
        first = lines[0][len("data: "):]
        self.assertIn('"object": "chat.completion.chunk"', first)


class ModelsEndpointTest(unittest.TestCase):
    def test_lists_installed_models(self):
        client = TestClient(server.app)
        body = client.get("/v1/models").json()
        self.assertEqual(body["object"], "list")
        self.assertTrue(body["data"])
        for item in body["data"]:
            self.assertIn("id", item)
            self.assertEqual(item["object"], "model")

    def test_placeholder_when_nothing_installed(self):
        with patch("server.discover_models", return_value=[]):
            body = TestClient(server.app).get("/v1/models").json()
        self.assertEqual(body["data"][0]["id"], "nexus-local")

    def test_health(self):
        self.assertEqual(TestClient(server.app).get("/health").json()["status"], "ok")


class ApiKeyTest(unittest.TestCase):
    def test_enforced_only_when_configured(self):
        client = TestClient(server.app)
        original = server.API_KEY
        try:
            server.API_KEY = "secret"
            denied = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            self.assertEqual(denied.status_code, 401)
            allowed = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer secret"},
            )
            self.assertEqual(allowed.status_code, 200)
        finally:
            server.API_KEY = original


class RuntimeChatTest(unittest.TestCase):
    def test_injected_backend_is_used(self):
        class Echo(SimulatedBackend):
            def chat(self, messages):
                return "echo-ok"

        runtime = server.NexusRuntime()
        runtime.gateway.set_backend(Echo())
        text, model = runtime.chat([{"role": "user", "content": "x"}])
        self.assertEqual(text, "echo-ok")

    def test_unreachable_backend_falls_back(self):
        from nexus.models.ollama import OllamaBackend

        runtime = server.NexusRuntime()
        runtime.gateway.set_backend(
            OllamaBackend("ghost-model", host="http://127.0.0.1:9")
        )
        text, model = runtime.chat([{"role": "user", "content": "x"}])
        self.assertEqual(model, "simulated_model")
        self.assertIn("[simulated]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class EntryPointTest(unittest.TestCase):
    """Tests drove the app through TestClient, so a broken main() shipped.

    These run the real entry point in a subprocess.
    """

    def test_main_binds_loopback_and_serves_health(self):
        import os
        import socket
        import subprocess
        import sys as _sys
        import time as _time
        import urllib.request

        def free_port():
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                return probe.getsockname()[1]

        repo = Path(__file__).resolve().parents[1]
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        port = free_port()
        env = {
            **os.environ,
            "NEXUS_CONFIG": str(Path(tmpdir.name) / "config.json"),
            "NEXUS_PORT": str(port),
            "NEXUS_HOST": "127.0.0.1",
        }
        proc = subprocess.Popen(
            [_sys.executable, "server.py"],
            cwd=str(repo), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(proc.stdout.close)
        try:
            deadline = _time.time() + 60
            body = None
            while _time.time() < deadline:
                if proc.poll() is not None:
                    self.fail(f"server exited early:\n{proc.stdout.read()}")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2
                    ) as response:
                        body = response.read().decode()
                    break
                except Exception:
                    _time.sleep(0.3)
            self.assertIsNotNone(body, "server never became healthy")
            self.assertIn('"status":"ok"', body)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
