"""Ollama backend.

All traffic goes through :func:`nexus.net.request` so the offline policy is
enforced in one place. Talking to `urlopen` directly here would have made the
offline guarantee advisory: any caller who pointed `host` at a remote address
would bypass every check.
"""

from nexus.net import NetworkPolicy, request


class OllamaBackend:
    def __init__(self, model, host="http://127.0.0.1:11434",
                 policy: NetworkPolicy | None = None, timeout: float = 180):
        self.model = model
        self.host = host.rstrip("/")
        self.policy = policy
        self.timeout = timeout

    def generate(self, prompt):
        data = self._post("/api/generate", {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        })
        return data["response"]

    def chat(self, messages, tools=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        data = self._post("/api/chat", payload)
        return data["message"]["content"]

    def chat_full(self, messages, tools=None):
        """Like chat() but returns the whole message.

        `chat()` drops `message.tool_calls` on the floor. Ollama does emit them
        when `tools` are supplied, and a host engine drives its whole loop off
        those calls, so the passthrough must not discard them.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        data = self._post("/api/chat", payload)
        return data.get("message") or {"role": "assistant", "content": ""}

    def _post(self, path, payload):
        return request(
            self.host + path,
            payload=payload,
            timeout=self.timeout,
            policy=self.policy,
        )
