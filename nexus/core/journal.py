"""Append-only execution journal and resumable checkpoints.

The journal is the durable record of what actually happened; checkpoints let an
interrupted task continue where it stopped instead of restarting from scratch.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class Journal:
    """One JSON line per event, flushed immediately so a crash cannot lose it."""

    def __init__(self, directory: Path | str, task_id: str | None = None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.path = self.dir / f"{self.task_id}.jsonl"

    def record(self, event: str, **fields) -> dict:
        entry = {"ts": time.time(), "task_id": self.task_id, "event": event, **fields}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text("utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries


class Checkpoint:
    """Serializable task progress, written atomically."""

    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.json"

    def save(self, task_id: str, state: dict) -> Path:
        target = self._path(task_id)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, target)  # atomic: never leaves a half-written checkpoint
        return target

    def load(self, task_id: str) -> dict | None:
        target = self._path(task_id)
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def clear(self, task_id: str) -> None:
        self._path(task_id).unlink(missing_ok=True)

    def list_tasks(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))
