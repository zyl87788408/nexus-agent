"""Shared test helpers: a real in-process model backend and isolated runtimes.

The `EchoBackend` is *not* a mock of the engine. It is a genuine `ModelBackend`
implementation that answers deterministically, so tests exercise the real
runtime/execution/tool paths without requiring Ollama to be installed. Anything
that can be tested without a model server is tested against the real code path.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.config import Config
from nexus.models.gateway import ModelBackend


class EchoBackend(ModelBackend):
    """Deterministic backend: planning returns JSON, execution echoes work."""

    model = "echo-model"

    def __init__(self, plan=None, answer_prefix="result"):
        self.plan = plan or ["step one", "step two"]
        self.answer_prefix = answer_prefix
        self.generate_calls = 0
        self.chat_calls = 0

    def generate(self, prompt: str) -> str:
        self.generate_calls += 1
        if "Step by step" in prompt or "steps" in prompt.lower():
            return json.dumps({"steps": self.plan})
        return f"{self.answer_prefix}: {prompt}"

    def chat(self, messages: list[dict]) -> str:
        self.chat_calls += 1
        last = messages[-1]["content"] if messages else ""
        return f"{self.answer_prefix}: {last}"


class FailingBackend(ModelBackend):
    model = "failing-model"

    def __init__(self, error=None, failures_before_success=0):
        self.error = error or RuntimeError("backend down")
        self.remaining = failures_before_success
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return json.dumps({"steps": ["only step"]})

    def chat(self, messages: list[dict]) -> str:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return "recovered result"


def temp_config(directory: Path, overrides: dict | None = None) -> Config:
    data = {
        "memory": {"db_path": str(directory / "memory.db")},
        "execution": {"checkpoint_dir": str(directory / "checkpoints")},
    }
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
    return Config(directory / "config.json", data)


def make_runtime(directory: Path | None = None, overrides: dict | None = None):
    """Build a runtime with isolated data paths."""
    from nexus.core.runtime import NexusRuntime

    if directory is None:
        directory = Path(tempfile.mkdtemp())
    return NexusRuntime(temp_config(Path(directory), overrides))
