"""Runtime configuration with persistence and environment overrides.

The configuration is the single source of truth for:
  * operating mode (offline / online / auto)
  * cloud provider credentials *references* (never the secrets themselves)
  * privacy policy that gates cloud egress
  * server binding and auth
  * execution limits

Secrets are never stored in the config file: only the *name* of the
environment variable holding them.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import sys
from enum import Enum
from pathlib import Path
from typing import Any


class Mode(str, Enum):
    """Operating mode. `AUTO` defers to the privacy policy at request time."""

    OFFLINE = "offline"
    ONLINE = "online"
    AUTO = "auto"

    @classmethod
    def parse(cls, value: Any) -> "Mode":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        raise ValueError(f"unknown mode {value!r}; expected one of "
                         f"{', '.join(m.value for m in cls)}")


REPO_ROOT = Path(__file__).resolve().parents[1]
if not REPO_ROOT.is_dir():
    # Running from a zipapp: __file__ points inside the archive, so keep mutable
    # state (data/, config.json) next to the .pyz instead of inside it.
    _launcher = Path(sys.argv[0]).resolve()
    REPO_ROOT = _launcher.parent if _launcher.is_file() else Path.cwd()
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("NEXUS_CONFIG") or (REPO_ROOT / "data" / "config.json")
)

DEFAULTS: dict[str, Any] = {
    "mode": Mode.AUTO.value,
    "cloud": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "NEXUS_CLOUD_API_KEY",
    },
    "privacy": {
        # Explicit user consent is required before any non-loopback request.
        "allow_cloud": False,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8085,
        "api_key_env": "NEXUS_API_KEY",
        "cors_origins": [
            "http://127.0.0.1",
            "http://localhost",
            "http://127.0.0.1:8085",
            "http://localhost:8085",
        ],
    },
    "execution": {
        "max_steps": 20,
        "max_retries": 2,
        "verify_results": True,
        "checkpoint_dir": "data/checkpoints",
    },
    "memory": {
        "db_path": "data/memory.db",
    },
    "hardware": {
        # Fraction of total RAM the engine may budget for model weights.
        "budget_fraction": 0.30,
        # RAM always reserved for the OS and the Python runtime itself.
        "reserved_mb": 900,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """Mutable configuration backed by a JSON file."""

    def __init__(self, path: Path | str | None = None, data: dict | None = None):
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._data = _deep_merge(DEFAULTS, data or {})
        self._apply_env_overrides()

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        target = Path(path) if path else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}
        if target.exists():
            try:
                data = json.loads(target.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                # A corrupt config must never prevent startup; fall back to defaults.
                data = {}
        return cls(target, data)

    def _apply_env_overrides(self) -> None:
        if os.environ.get("NEXUS_MODE"):
            self._data["mode"] = Mode.parse(os.environ["NEXUS_MODE"]).value
        if os.environ.get("NEXUS_CLOUD_BASE_URL"):
            self._data["cloud"]["base_url"] = os.environ["NEXUS_CLOUD_BASE_URL"]
        if os.environ.get("NEXUS_CLOUD_MODEL"):
            self._data["cloud"]["model"] = os.environ["NEXUS_CLOUD_MODEL"]
        if os.environ.get("NEXUS_ALLOW_CLOUD", "").lower() in {"1", "true", "yes"}:
            self._data["privacy"]["allow_cloud"] = True
        if os.environ.get("NEXUS_HOST"):
            self._data["server"]["host"] = os.environ["NEXUS_HOST"]
        if os.environ.get("NEXUS_PORT"):
            self._data["server"]["port"] = int(os.environ["NEXUS_PORT"])

    # ------------------------------------------------------------------ save
    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target

    # --------------------------------------------------------------- access
    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def get(self, dotted: str, default=None):
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TypeError(f"config path {dotted!r} collides with a scalar")
        node[parts[-1]] = value

    # ------------------------------------------------------------ properties
    @property
    def mode(self) -> Mode:
        return Mode.parse(self._data["mode"])

    @mode.setter
    def mode(self, value: Mode | str) -> None:
        self._data["mode"] = Mode.parse(value).value

    @property
    def allow_cloud(self) -> bool:
        return bool(self._data["privacy"].get("allow_cloud"))

    @allow_cloud.setter
    def allow_cloud(self, value: bool) -> None:
        self._data["privacy"]["allow_cloud"] = bool(value)

    @property
    def cloud_enabled(self) -> bool:
        return bool(self._data["cloud"].get("enabled"))

    def cloud_api_key(self) -> str | None:
        """Resolve the cloud API key.

        Order: environment variable first (deployment-friendly), then a
        dedicated file. The key is deliberately NOT stored in config.json:
        config.json gets attached to diagnostics bundles, and a key in there
        would be emailed around in plaintext.
        """
        env_name = self._data["cloud"].get("api_key_env")
        value = os.environ.get(env_name) if env_name else None
        if value:
            return value
        key_file = self.path.parent / "cloud.key"
        try:
            if key_file.is_file():
                return key_file.read_text("utf-8").strip() or None
        except OSError:
            return None
        return None

    def save_cloud_key(self, key: str) -> Path:
        key_file = self.path.parent / "cloud.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key.strip(), "utf-8")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return key_file

    def server_api_key(self) -> str | None:
        env_name = self._data["server"].get("api_key_env")
        value = os.environ.get(env_name) if env_name else None
        if value:
            return value
        key_file = self.path.parent / "api.key"
        try:
            if key_file.is_file():
                return key_file.read_text("utf-8").strip() or None
        except OSError:
            return None
        return None

    def ensure_server_api_key(self) -> str:
        """Return the server key, generating one on first run.

        The service used to come up with no authentication at all, which made
        every endpoint - including the one that runs tools - reachable by any
        page open in the user's browser. A per-install key file keeps the
        "just run it" experience: local clients read the same file.
        """
        existing = self.server_api_key()
        if existing:
            return existing
        key = secrets.token_urlsafe(32)
        key_file = self.path.parent / "api.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, "utf-8")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return key

    def resolve_path(self, relative: str) -> Path:
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else REPO_ROOT / candidate
