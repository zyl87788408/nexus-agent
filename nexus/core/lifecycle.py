from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    goal: str
    max_steps: int = 20
    max_retries: int = 2
    status: TaskStatus = TaskStatus.PENDING
    steps: int = 0
    error: str = ""
    created_at: str = ""
    task_id: str = ""
    results: list = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.task_id:
            self.task_id = uuid.uuid4().hex[:12]

    def start(self):
        if self.status != TaskStatus.PENDING:
            raise RuntimeError("Task cannot be started")
        self.status = TaskStatus.RUNNING

    def complete(self):
        self.status = TaskStatus.COMPLETED

    def fail(self, error):
        self.error = str(error)
        self.status = TaskStatus.FAILED

    def cancel(self):
        if self.status == TaskStatus.RUNNING:
            self.status = TaskStatus.CANCELLED

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status.value,
            "steps": self.steps,
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
            "error": self.error,
            "created_at": self.created_at,
        }
