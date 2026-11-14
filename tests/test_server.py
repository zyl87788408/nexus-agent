import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import server
from nexus.core.runtime import SimulatedBackend


class ChatCompletionsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        server.runtime.gateway.set_backend(None)

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

    def test_simulated_fallback_without_models(self):
        body = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "没有模型时也要能答"}]},
        ).json()
        self.assertEqual(body["model"], "simulated_model")
        self.assertIn("没有模型时也要能答", body["choices"][0]["message"]["content"])

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
    def test_lists_nexus_model(self):
        client = TestClient(server.app)
        body = client.get("/v1/models").json()
        self.assertEqual(body["object"], "list")
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
