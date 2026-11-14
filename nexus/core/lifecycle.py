from enum import Enum
from dataclasses import dataclass
from datetime import datetime

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
    status: TaskStatus = TaskStatus.PENDING
    steps: int = 0
    error: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

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
