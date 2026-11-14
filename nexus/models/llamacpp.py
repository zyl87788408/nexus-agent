"""llama.cpp (server) backend. Traffic is routed through the single egress point."""

from nexus.net import NetworkPolicy, request


class LlamaCppBackend:
    def __init__(self, model="local", host="http://127.0.0.1:8080",
                 policy: NetworkPolicy | None = None, timeout: float = 180):
        self.model = model
        self.host = host.rstrip("/")
        self.policy = policy
        self.timeout = timeout

    def generate(self, prompt):
        return self.chat([{"role": "user", "content": prompt}])

    def chat(self, messages):
        result = request(
            self.host + "/v1/chat/completions",
            payload={
                "model": self.model,
                "messages": messages,
                "stream": False,
            },
            timeout=self.timeout,
            policy=self.policy,
        )
        return result["choices"][0]["message"]["content"]
