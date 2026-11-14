"""Single egress point for every outbound request NEXUS makes.

Routing all network access through :func:`request` is what makes the offline
guarantee *real* rather than a promise: when the mode is OFFLINE any
non-loopback target raises :class:`CloudBlockedError` before a socket is opened.

Loopback targets (Ollama / llama.cpp running on the same machine) are always
permitted, because they are part of the local, offline execution path.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from nexus.config import Mode


class CloudBlockedError(RuntimeError):
    """Raised when policy forbids reaching a non-loopback endpoint."""


class NetworkError(RuntimeError):
    """Transport-level failure (connection refused, timeout, bad status)."""


def _host_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise CloudBlockedError(f"unsupported scheme in {url!r}")
    return parsed.hostname or ""


def is_loopback(url_or_host: str) -> bool:
    """True when the target resolves to the local machine."""
    host = _host_of(url_or_host) if "://" in url_or_host else url_or_host
    host = (host or "").strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A bare hostname: resolve it, and require every address to be loopback.
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            # Unresolvable names are not local. Treating them as remote keeps the
            # offline guarantee intact (fail closed).
            return False
        return bool(infos) and all(
            ipaddress.ip_address(info[4][0]).is_loopback for info in infos
        )


class NetworkPolicy:
    """Decides whether a request is allowed under the current mode."""

    def __init__(self, mode: Mode | str = Mode.OFFLINE, allow_cloud: bool = False):
        self.mode = Mode.parse(mode)
        self.allow_cloud = bool(allow_cloud)

    def allows(self, url: str) -> bool:
        if is_loopback(url):
            return True
        if self.mode is Mode.OFFLINE:
            return False
        # ONLINE and AUTO both need explicit user consent for cloud egress.
        return self.allow_cloud

    def check(self, url: str) -> None:
        if not self.allows(url):
            if self.mode is Mode.OFFLINE:
                raise CloudBlockedError(
                    f"offline mode forbids reaching {url!r}; "
                    "switch to online/auto and enable cloud access to use it"
                )
            raise CloudBlockedError(
                f"cloud access is not authorized; set privacy.allow_cloud "
                f"(NEXUS_ALLOW_CLOUD=1) to reach {url!r}"
            )


def request(
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 60,
    method: str | None = None,
    policy: NetworkPolicy | None = None,
) -> dict:
    """Perform a JSON request, enforcing the network policy first."""
    policy = policy or NetworkPolicy()
    policy.check(url)

    body = None
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})

    req = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise NetworkError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"cannot reach {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise NetworkError(f"cannot reach {url}: {exc}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NetworkError(f"invalid JSON from {url}") from exc


def probe(url: str, *, timeout: float = 1.5, policy: NetworkPolicy | None = None) -> bool:
    """Cheap liveness check. Returns False instead of raising."""
    try:
        request(url, timeout=timeout, policy=policy)
        return True
    except (CloudBlockedError, NetworkError):
        return False
