"""Confinement for tool execution.

Two independent controls:
  * a filesystem jail - every path is resolved and must stay inside the root;
  * a deny-list of dangerous operations that no tool may perform by default.

The deny-list targets *effects* (escaping the jail, spawning shells, touching
credentials) rather than tool names, so a newly added tool cannot accidentally
inherit dangerous behaviour.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


class SandboxViolation(PermissionError):
    """A tool attempted something the sandbox does not allow."""


_DENIED_COMMANDS = [
    r"\brm\s+-rf\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",           # fork bomb
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bchmod\s+777\s+/",
    r">\s*/dev/sd",
    r"\bcurl\b.*\|\s*(ba)?sh",
    r"\bwget\b.*\|\s*(ba)?sh",
]

_SENSITIVE_PATH_PARTS = {
    ".ssh", ".gnupg", ".aws", ".kube", ".docker",
    "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "credentials.json",
}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Sandbox:
    def __init__(self, root: Path | str, *, allow_shell: bool = False,
                 max_read_bytes: int = 1_000_000):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow_shell = allow_shell
        self.max_read_bytes = max_read_bytes

    def resolve(self, path: str | Path) -> Path:
        """Resolve `path` inside the jail, rejecting traversal and secrets."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise SandboxViolation(f"cannot resolve {path!r}: {exc}") from exc

        if not _is_within(resolved, self.root):
            raise SandboxViolation(
                f"path {resolved} escapes sandbox root {self.root}"
            )
        for part in resolved.relative_to(self.root).parts:
            if part.lower() in _SENSITIVE_PATH_PARTS:
                raise SandboxViolation(f"access to {part!r} is not permitted")
        return resolved

    def read_text(self, path: str) -> str:
        target = self.resolve(path)
        if not target.exists():
            raise FileNotFoundError(target)
        size = target.stat().st_size
        if size > self.max_read_bytes:
            raise SandboxViolation(
                f"{target.name} is {size} bytes, above the {self.max_read_bytes} limit"
            )
        return target.read_text("utf-8", errors="replace")

    def write_text(self, path: str, content: str) -> Path:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, "utf-8")
        return target

    def list_dir(self, path: str = ".") -> list[str]:
        target = self.resolve(path)
        if not target.is_dir():
            raise NotADirectoryError(target)
        return sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())

    def check_command(self, command: str) -> None:
        """Validate a shell command before it is ever executed."""
        if not self.allow_shell:
            raise SandboxViolation(
                "shell execution is disabled; enable it explicitly in config"
            )
        lowered = command.lower()
        for pattern in _DENIED_COMMANDS:
            if re.search(pattern, lowered):
                raise SandboxViolation(
                    f"command matches a denied pattern ({pattern})"
                )
        for part in re.split(r"[\s;|&<>]+", command):
            cleaned = part.strip("\"'")
            if not cleaned or cleaned.startswith("-"):
                continue
            if any(s in cleaned.lower() for s in _SENSITIVE_PATH_PARTS):
                raise SandboxViolation(f"command touches sensitive path {cleaned!r}")
            if cleaned.startswith("/") or cleaned.startswith("~"):
                self.resolve(cleaned)


def default_sandbox_root(base: Path | str | None = None) -> Path:
    if base:
        return Path(base)
    return Path(os.environ.get("NEXUS_SANDBOX") or (Path.cwd() / "data" / "sandbox"))
