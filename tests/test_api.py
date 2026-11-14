"""HTTP surface: OpenAI compatibility, mode switching, tools, memory, auth."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import server
from nexus.core.runtime import SimulatedBackend
from tests.helpers import EchoBackend


class ModeEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.original_config = server.config

    def tearDown(self):
        server.config = self.original_config

    def test_lists_all_modes_and_the_current_one(self):
        body = self.client.get("/api/modes").json()
        self.assertEqual(set(body["modes"]), {"offline", "online", "auto"})
        self.assertIn(body["current"], body["modes"])

    def test_switching_reports_verification_result(self):
        body = self.client.post("/api/mode",
                                json={"mode": "offline", "persist": False}).json()
        self.assertEqual(body["mode"], "offline")
        self.assertIn("verified", body)
        self.assertIn("detail", body)

    def test_invalid_mode_is_rejected_with_400(self):
        response = self.client.post("/api/mode",
                                    json={"mode": "warp", "persist": False})
        self.assertEqual(response.status_code, 400)

    def test_status_endpoint_exposes_capabilities(self):
        body = self.client.get("/api/status").json()
        for field in ("mode", "tools", "plugins", "memory_entries"):
            self.assertIn(field, body)


class ToolEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_lists_tools_with_scopes(self):
        body = self.client.get("/api/tools").json()
        names = {t["name"] for t in body["tools"]}
        self.assertIn("read_file", names)
        self.assertIn("run_shell", names)

    def test_tool_call_performs_real_work(self):
        server.runtime.tools.sandbox.write_text("api-test.txt", "value")
        body = self.client.post("/api/tool", json={
            "tool": "read_file", "arguments": {"path": "api-test.txt"}}).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["output"], "value")

    def test_tool_call_outside_sandbox_is_refused(self):
        body = self.client.post("/api/tool", json={
            "tool": "read_file", "arguments": {"path": "/etc/passwd"}}).json()
        self.assertFalse(body["ok"])
        self.assertIn("sandbox", body["error"].lower())

    def test_shell_tool_refused_without_grant(self):
        body = self.client.post("/api/tool", json={
            "tool": "run_shell", "arguments": {"command": "echo hi"}}).json()
        self.assertFalse(body["ok"])


class MemoryEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_add_search_delete_cycle(self):
        created = self.client.post("/api/memory", json={
            "content": "api memory entry", "kind": "note"}).json()
        self.assertTrue(created["stored"])
        memory_id = created["id"]

        found = self.client.get("/api/memory/search", params={"q": "api memory"}).json()
        self.assertTrue(any(r["id"] == memory_id for r in found["results"]))

        deleted = self.client.delete(f"/api/memory/{memory_id}").json()
        self.assertTrue(deleted["deleted"])

    def test_empty_content_is_rejected(self):
        response = self.client.post("/api/memory", json={"content": "   "})
        self.assertEqual(response.status_code, 400)


class TaskEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.original_backend = server.runtime.gateway.backend
        server.runtime.gateway.set_backend(EchoBackend(plan=["one step"]))

    def tearDown(self):
        server.runtime.gateway.set_backend(self.original_backend)

    def test_task_endpoint_returns_a_real_result(self):
        body = self.client.post("/api/task", json={"goal": "do the thing"}).json()
        self.assertEqual(body["status"], "completed")
        self.assertFalse(body["simulated"])
        self.assertIn("result:", body["output"])

    def test_empty_goal_is_rejected(self):
        response = self.client.post("/api/task", json={"goal": "  "})
        self.assertEqual(response.status_code, 400)

    def test_team_endpoint_returns_specialist_outputs(self):
        body = self.client.post("/api/team", json={"goal": "review it"}).json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["subtasks"]), 3)


class OpenAIContractTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.original_backend = server.runtime.gateway.backend

    def tearDown(self):
        server.runtime.gateway.set_backend(self.original_backend)

    def test_chat_completion_shape(self):
        server.runtime.gateway.set_backend(EchoBackend())
        body = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}]}).json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertTrue(body["id"].startswith("chatcmpl-"))
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        usage = body["usage"]
        self.assertEqual(usage["total_tokens"],
                         usage["prompt_tokens"] + usage["completion_tokens"])

    def test_response_declares_whether_it_is_simulated(self):
        server.runtime.gateway.set_backend(None)
        body = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}]}).json()
        self.assertIn("nexus", body)
        self.assertEqual(body["nexus"]["simulated"], body["model"] == "simulated_model")

    def test_models_endpoint(self):
        body = self.client.get("/v1/models").json()
        self.assertEqual(body["object"], "list")
        self.assertTrue(body["data"])

    def test_streaming_sse_frames(self):
        server.runtime.gateway.set_backend(EchoBackend())
        with self.client.stream(
            "POST", "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as response:
            self.assertEqual(response.status_code, 200)
            lines = [line for line in response.iter_lines() if line.startswith("data: ")]
        self.assertTrue(lines)
        self.assertEqual(lines[-1], "data: [DONE]")
        self.assertIn("chat.completion.chunk", lines[0])


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.original_key = server.API_KEY
        self.original_config = server.config

    def tearDown(self):
        server.API_KEY = self.original_key
        server.config = self.original_config

    def test_key_required_when_configured(self):
        server.API_KEY = "topsecret"
        denied = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(denied.status_code, 401)

    def test_correct_key_allows_access(self):
        server.API_KEY = "topsecret"
        allowed = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer topsecret"},
        )
        self.assertEqual(allowed.status_code, 200)

    def test_wrong_key_is_rejected(self):
        server.API_KEY = "topsecret"
        denied = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(denied.status_code, 401)

    def test_mutating_endpoints_require_the_key(self):
        server.API_KEY = "topsecret"
        for path, payload in (
            ("/api/mode", {"mode": "offline", "persist": False}),
            ("/api/task", {"goal": "x"}),
            ("/api/tool", {"tool": "read_file", "arguments": {"path": "a"}}),
            ("/api/memory", {"content": "x"}),
        ):
            response = self.client.post(path, json=payload)
            self.assertEqual(response.status_code, 401, path)

    def test_public_endpoints_stay_readable(self):
        server.API_KEY = "topsecret"
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/v1/models").status_code, 200)

    def test_refuses_public_bind_without_key(self):
        original_key = server.API_KEY
        server.API_KEY = None
        try:
            server.config.set("server.host", "0.0.0.0")
            self.assertEqual(server.main(), 2)
        finally:
            server.API_KEY = original_key
            server.config.set("server.host", "127.0.0.1")

    def test_cors_is_not_wildcard(self):
        origins = server.config.get("server.cors_origins")
        self.assertNotIn("*", origins)

    def test_disallowed_origin_gets_no_cors_header(self):
        response = self.client.get("/health", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", {
            k.lower() for k in response.headers})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PluginEndpointTest(unittest.TestCase):
    """The HTTP surface of enable/disable, including auth and 404 handling."""

    def setUp(self):
        self.client = TestClient(server.app)
        self.plugins = server.runtime.plugins
        self.saved_key = server.API_KEY
        self.original_disabled = set(self.plugins.disabled)
        self.original_config = self.plugins.config

    def tearDown(self):
        self.plugins.disabled = self.original_disabled
        self.plugins.config = self.original_config
        server.API_KEY = self.saved_key

    def _auth(self):
        return {"Authorization": f"Bearer {server.API_KEY}"} if server.API_KEY else {}

    def test_status_reports_a_disabled_list(self):
        body = self.client.get("/api/plugins", headers=self._auth()).json()
        self.assertIn("disabled", body)
        self.assertIsInstance(body["disabled"], list)

    def test_unknown_plugin_disable_returns_404(self):
        response = self.client.post("/api/plugins/no-such-plugin/disable",
                                    headers=self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["error"]["message"])

    def test_unknown_plugin_enable_returns_404(self):
        response = self.client.post("/api/plugins/no-such-plugin/enable",
                                    headers=self._auth())
        self.assertEqual(response.status_code, 404)

    def test_state_endpoint_requires_auth_when_a_key_is_configured(self):
        server.API_KEY = "secret-key"
        response = self.client.post("/api/plugins/no-such-plugin/disable")
        self.assertEqual(response.status_code, 401)

    def test_state_endpoint_toggles_via_the_generic_route(self):
        from nexus.adapters.manager import PluginManager
        from nexus.tools.registry import build_default_registry

        with tempfile.TemporaryDirectory() as d:
            plugin_dir = Path(d) / "demo"
            plugin_dir.mkdir()
            (plugin_dir / "manifest.json").write_text(
                '{"name": "demo", "version": "1.0", "host": "*",'
                ' "permissions": ["tools"], "entry": "plugin.py"}'
            )
            (plugin_dir / "plugin.py").write_text(
                "def setup(ctx):\n"
                "    from nexus.tools.registry import ToolSpec\n"
                "    ctx.services['tools'].register(ToolSpec(\n"
                "        name='demo_tool', description='x', scope='read',\n"
                "        handler=lambda: 'ran'))\n"
            )
            registry = build_default_registry()
            manager = PluginManager(d, services={"tools": registry})
            manager.activate_all()
            server.runtime.plugins = manager
            try:
                body = self.client.post("/api/plugins/demo", json={"enabled": False},
                                        headers=self._auth()).json()
                self.assertEqual(body["disabled"], ["demo"])
                self.assertEqual(body["active"], [])
                self.assertNotIn("demo_tool", registry.names())

                body = self.client.post("/api/plugins/demo", json={"enabled": True},
                                        headers=self._auth()).json()
                self.assertEqual(body["disabled"], [])
                self.assertEqual(body["active"], ["demo"])
                self.assertIn("demo_tool", registry.names())
            finally:
                server.runtime.plugins = self.plugins


class ToolCallPassthroughTest(unittest.TestCase):
    """A host engine drives its loop through `tool_calls`.

    NEXUS sits at the model-API layer, so it must forward the host's tool
    schemas and return OpenAI-shaped `tool_calls`. Before this, `tools` was
    dropped by the request model and the Ollama backend returned only
    `message["content"]`, so every agent turn silently degraded to plain chat
    while `finish_reason` still said "stop" - a failure the host cannot detect.
    """

    TOOLS = [{
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Execute a shell command",
            "parameters": {"type": "object",
                           "properties": {"command": {"type": "string"}},
                           "required": ["command"]},
        },
    }]

    def setUp(self):
        self.client = TestClient(server.app)
        self.original_runtime = server.runtime

    def tearDown(self):
        server.runtime = self.original_runtime

    class _ToolCallingBackend:
        """Emits an Ollama-shaped tool_calls message, like the real backend."""
        model = "fake_tool_model"

        def __init__(self):
            self.seen_tools = None

        def chat(self, messages, tools=None):
            return ""

        def chat_full(self, messages, tools=None):
            self.seen_tools = tools
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {"name": "run_shell",
                                 "arguments": {"command": "ls"}},
                }],
            }

    def _install(self, backend):
        server.runtime.gateway.set_backend(backend)
        server.runtime._simulated = None
        return backend

    def test_tools_reach_the_backend_unchanged(self):
        backend = self._install(self._ToolCallingBackend())
        self.client.post("/v1/chat/completions", headers={}, json={
            "messages": [{"role": "user", "content": "list files"}],
            "tools": self.TOOLS,
        })
        self.assertIsNotNone(backend.seen_tools, "tools were never forwarded")
        self.assertEqual(backend.seen_tools[0]["function"]["name"], "run_shell")

    def test_response_carries_openai_shaped_tool_calls(self):
        self._install(self._ToolCallingBackend())
        body = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "list files"}],
            "tools": self.TOOLS,
        }).json()

        choice = body["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        calls = choice["message"]["tool_calls"]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "run_shell")
        self.assertIsInstance(call["id"], str)
        self.assertTrue(call["id"])
        # OpenAI sends `arguments` as a JSON *string*, not an object.
        self.assertIsInstance(call["function"]["arguments"], str)
        self.assertEqual(json.loads(call["function"]["arguments"]),
                         {"command": "ls"})

    def test_no_tool_calls_still_reports_stop(self):
        class PlainBackend:
            model = "plain"

            def chat(self, messages, tools=None):
                return "just talking"

            def chat_full(self, messages, tools=None):
                return {"role": "assistant", "content": "just talking"}

        self._install(PlainBackend())
        body = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": self.TOOLS,
        }).json()
        choice = body["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertNotIn("tool_calls", choice["message"])
        self.assertEqual(choice["message"]["content"], "just talking")

    def test_missing_tool_call_id_is_synthesised(self):
        """Ollama omits `id`; hosts key their tool results off it."""
        self._install(self._ToolCallingBackend())
        body = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "list files"}],
            "tools": self.TOOLS,
        }).json()
        call = body["choices"][0]["message"]["tool_calls"][0]
        self.assertTrue(call["id"].startswith("call_"))
