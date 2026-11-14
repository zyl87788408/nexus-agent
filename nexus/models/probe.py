"""Decide whether a discovered model can *actually* run.

Listing a model is not evidence that it loads. This module performs a real,
minimal inference call and classifies the outcome, so the selector never
advertises a model that would fail on first use.
"""

from __future__ import annotations

from nexus.net import NetworkPolicy, NetworkError, request

_PROBE_PROMPT = "ok"
_CACHE_TTL_SECONDS = 30.0
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def clear_probe_cache() -> None:
    _cache.clear()


def probe_model(backend: str, name: str, *, timeout: float = 20,
                policy: NetworkPolicy | None = None,
                use_cache: bool = True) -> dict:
    """Run a tiny real inference. Returns {available, reason, latency_ms}.

    A short TTL cache avoids re-probing the same model on every request, which
    on CPU-only hardware costs seconds each time. Availability is re-checked
    once the entry expires, so a stopped server is still detected.
    """
    import time

    key = (backend, name)
    if use_cache:
        cached = _cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return dict(cached[1])

    result = _probe_uncached(backend, name, timeout=timeout, policy=policy)
    if use_cache:
        _cache[key] = (time.time(), dict(result))
    return result


def _probe_uncached(backend: str, name: str, *, timeout: float,
                    policy: NetworkPolicy | None) -> dict:
    import time

    started = time.time()
    try:
        if backend == "ollama":
            data = request(
                "http://127.0.0.1:11434/api/generate",
                payload={"model": name, "prompt": _PROBE_PROMPT, "stream": False,
                         "options": {"num_predict": 1}},
                timeout=timeout,
                policy=policy,
            )
            ok = bool(data.get("response") is not None)
            reason = "" if ok else "empty response"
        elif backend == "llama.cpp":
            data = request(
                "http://127.0.0.1:8080/v1/chat/completions",
                payload={"model": name, "max_tokens": 1,
                         "messages": [{"role": "user", "content": _PROBE_PROMPT}]},
                timeout=timeout,
                policy=policy,
            )
            choices = data.get("choices") or []
            ok = bool(choices and choices[0].get("message"))
            reason = "" if ok else "no choices in response"
        else:
            return {"available": False, "reason": f"unknown backend {backend!r}",
                    "latency_ms": 0}
    except NetworkError as exc:
        return {"available": False, "reason": str(exc), "latency_ms": 0}
    except (KeyError, TypeError, ValueError) as exc:
        return {"available": False, "reason": f"malformed response: {exc}",
                "latency_ms": 0}

    return {
        "available": ok,
        "reason": reason,
        "latency_ms": int((time.time() - started) * 1000),
    }
