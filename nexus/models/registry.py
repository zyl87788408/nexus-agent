"""Discover locally served models (Ollama, llama.cpp).

Discovery only *lists* models. Whether a model can actually run is decided
separately by :mod:`nexus.models.probe`, because a model present in the API
listing may still fail to load (corrupt blob, insufficient RAM).
"""

from __future__ import annotations

import json

from nexus.net import NetworkPolicy, NetworkError, request

OLLAMA_HOST = "http://127.0.0.1:11434"
LLAMACPP_HOST = "http://127.0.0.1:8080"


def _fetch(url: str, policy: NetworkPolicy | None = None):
    try:
        return request(url, timeout=3, policy=policy)
    except NetworkError:
        return None


def discover_models(policy: NetworkPolicy | None = None) -> list[dict]:
    models: list[dict] = []

    data = _fetch(f"{OLLAMA_HOST}/api/tags", policy)
    if data:
        for item in data.get("models", []) or []:
            details = item.get("details") or {}
            models.append(
                {
                    "backend": "ollama",
                    "name": item.get("name", ""),
                    "size_bytes": item.get("size"),
                    "parameters": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "family": details.get("family"),
                    "digest": item.get("digest"),
                }
            )

    data = _fetch(f"{LLAMACPP_HOST}/v1/models", policy)
    if data:
        for item in data.get("data", []) or []:
            models.append(
                {
                    "backend": "llama.cpp",
                    "name": item.get("id", ""),
                    # llama.cpp's /v1/models does not expose size metadata.
                    "size_bytes": None,
                    "parameters": None,
                    "quantization": None,
                    "family": None,
                    "digest": None,
                }
            )

    return models


if __name__ == "__main__":
    print(json.dumps(discover_models(), indent=2, ensure_ascii=False))
