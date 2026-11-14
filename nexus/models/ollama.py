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

    def chat(self, messages):
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
        })
        return data["message"]["content"]

    def _post(self, path, payload):
        return request(
            self.host + path,
            payload=payload,
            timeout=self.timeout,
            policy=self.policy,
        )
