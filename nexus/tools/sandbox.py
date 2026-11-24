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
import shlex
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

# Programs run_shell may start. The old design passed the whole string to a
# shell, so `echo `id`` and `sh -c ...` were enough to leave the jail - a
# deny-list of substrings cannot express "no shell syntax". Arguments are now
# handed to the process directly, so the only question left is which programs
# are worth exposing at all.
_ALLOWED_EXECUTABLES = {
    "echo", "cat", "ls", "dir", "pwd", "head", "tail", "wc", "grep", "find",
    "sort", "uniq", "cut", "tr", "sed", "awk", "python", "python3",
}

# Interpreters get extra scrutiny: only a script path is acceptable.
_INTERPRETERS = {"python", "python3"}

# Characters that mean something to a shell but nothing to an argv list. Their
# presence signals that the caller expected shell features we no longer offer,
# so refuse loudly instead of silently running something different.
_SHELL_METACHARACTERS = set("|&;<>$`\\\n\r*?[]{}~!#")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Sandbox:
    def __init__(self, root: Path | str, *, allow_shell: bool = False,
                 max_read_bytes: int = 1_000_000, shell_timeout: int = 30):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow_shell = allow_shell
        self.max_read_bytes = max_read_bytes
        self.shell_timeout = shell_timeout

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
        """Validate a shell command string before it is ever executed."""
        self.prepare_command(command)

    def prepare_command(self, command: str) -> list[str]:
        """Validate `command` and return the argv that should be executed.

        No shell is involved: the string is split into an argument vector and
        handed straight to the program. That removes command substitution,
        pipes, redirection and `sh -c` as escape routes, because those only
        mean anything to a shell.
        """
        if not self.allow_shell:
            raise SandboxViolation(
                "shell execution is disabled; enable it explicitly in config"
            )
        if not command or not command.strip():
            raise SandboxViolation("empty command")
        if len(command) > 4_000:
            raise SandboxViolation("command is too long")

        # Quotes are stripped below, so look for metacharacters outside them.
        outside_quotes = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
        offenders = sorted(set(outside_quotes) & _SHELL_METACHARACTERS)
        if offenders:
            raise SandboxViolation(
                "shell syntax is not supported; arguments are passed directly "
                f"(found {''.join(offenders)!r})"
            )

        lowered = command.lower()
        for pattern in _DENIED_COMMANDS:
            if re.search(pattern, lowered):
                raise SandboxViolation(
                    f"command matches a denied pattern ({pattern})"
                )

        argv = shlex.split(command)
        if not argv:
            raise SandboxViolation("empty command")
        program = os.path.basename(argv[0]).lower()
        if program.endswith(".exe"):
            program = program[:-4]
        if program not in _ALLOWED_EXECUTABLES:
            raise SandboxViolation(
                f"{argv[0]!r} is not an allowed program; "
                f"permitted: {', '.join(sorted(_ALLOWED_EXECUTABLES))}"
            )

        for part in argv:
            cleaned = part.strip("\"'")
            if any(s in cleaned.lower() for s in _SENSITIVE_PATH_PARTS):
                raise SandboxViolation(f"command touches sensitive path {cleaned!r}")
            if cleaned.startswith("/") or cleaned.startswith("~") or \
                    cleaned.startswith(".."):
                self.resolve(cleaned)

        if program in _INTERPRETERS:
            self._check_interpreter(argv)
        return argv

    def _check_interpreter(self, argv: list[str]) -> None:
        """Allow running a script from the jail, never inline code.

        ``python -c '...'`` is a full interpreter with no jail around it, which
        would make the argv rewrite pointless. Only a script path is accepted.
        """
        for arg in argv[1:]:
            if arg in {"-c", "-m"} or arg.startswith(("-c", "-m")):
                raise SandboxViolation(
                    "inline code execution (-c/-m) is not permitted; "
                    "write a script inside the sandbox and run that"
                )
            if arg.startswith("-"):
                continue
            self.resolve(arg)
            return
        raise SandboxViolation("no script path given")


def default_sandbox_root(base: Path | str | None = None) -> Path:
    if base:
        return Path(base)
    return Path(os.environ.get("NEXUS_SANDBOX") or (Path.cwd() / "data" / "sandbox"))
