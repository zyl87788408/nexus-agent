"""OpenAI-compatible cloud backend.

Every call travels through :mod:`nexus.net`, so offline mode cannot reach it
even if the credentials and URL are present.
"""

from __future__ import annotations

from nexus.net import CloudBlockedError, NetworkError, NetworkPolicy, request


class CloudBackend:
    """Calls any OpenAI-compatible endpoint (OpenAI, DeepSeek, vLLM, ...)."""

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, policy: NetworkPolicy | None = None,
                 timeout: float = 60):
        self.model = model or self.DEFAULT_MODEL
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.policy = policy
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _require_key(self) -> None:
        if not self.api_key:
            raise NetworkError(
                "cloud API key missing: set the environment variable named by "
                "cloud.api_key_env (default NEXUS_CLOUD_API_KEY)"
            )

    def chat(self, messages: list[dict], tools=None) -> str:
        self._require_key()
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        data = request(
            self.endpoint,
            payload=payload,
            headers=self._headers(),
            timeout=self.timeout,
            policy=self.policy,
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NetworkError(f"malformed cloud response: {exc}") from exc

    def chat_full(self, messages: list[dict], tools=None) -> dict:
        """Return the full assistant message, including `tool_calls`."""
        self._require_key()
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        data = request(
            self.endpoint,
            payload=payload,
            headers=self._headers(),
            timeout=self.timeout,
            policy=self.policy,
        )
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NetworkError(f"malformed cloud response: {exc}") from exc

    def generate(self, prompt: str) -> str:
        return self.chat([{"role": "user", "content": prompt}])

    def probe(self) -> dict:
        """Verify the endpoint is reachable and the key is accepted."""
        try:
            data = request(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=self.timeout,
                policy=self.policy,
            )
        except CloudBlockedError as exc:
            return {"available": False, "reason": str(exc), "blocked": True}
        except NetworkError as exc:
            return {"available": False, "reason": str(exc), "blocked": False}
        return {"available": True, "reason": "", "blocked": False,
                "models": [m.get("id") for m in data.get("data", [])][:20]}
