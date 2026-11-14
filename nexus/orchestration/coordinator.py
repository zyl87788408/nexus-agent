"""Multi-agent coordination with bounded concurrency.

Design goals:
  * a main agent splits a goal into specialist sub-tasks;
  * specialists run concurrently, but never more than the hardware allows;
  * results are aggregated, with failures isolated rather than fatal;
  * conflicting answers are detected by comparing normalised outputs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from nexus.agents.base import DEFAULT_PROFILES, AgentProfile


@dataclass
class SubTask:
    goal: str
    agent: str
    result: str = ""
    error: str = ""
    ok: bool = False


@dataclass
class TeamResult:
    goal: str
    subtasks: list[SubTask] = field(default_factory=list)
    summary: str = ""
    conflicts: list[str] = field(default_factory=list)
    ok: bool = False

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "ok": self.ok,
            "summary": self.summary,
            "conflicts": self.conflicts,
            "subtasks": [
                {"goal": s.goal, "agent": s.agent, "ok": s.ok,
                 "output": s.result, "error": s.error}
                for s in self.subtasks
            ],
        }


class Coordinator:
    """Assigns work to specialists and merges their answers."""

    def __init__(self, chat_fn, profiles: dict[str, AgentProfile] | None = None,
                 max_parallel: int = 1):
        self.chat = chat_fn
        self.profiles = profiles or DEFAULT_PROFILES
        self.max_parallel = max(1, int(max_parallel))

    # ------------------------------------------------------------- planning
    def plan(self, goal: str, agents: list[str] | None = None) -> list[SubTask]:
        """Split a goal into one sub-task per specialist (low-resource friendly)."""
        chosen = agents or list(self.profiles)
        if not chosen:
            return [SubTask(goal=goal, agent="default")]
        return [
            SubTask(goal=goal, agent=agent)
            for agent in chosen
            if agent in self.profiles
        ]

    # ------------------------------------------------------------ execution
    def _run_one(self, task: SubTask) -> SubTask:
        profile = self.profiles[task.agent]
        messages = [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": task.goal},
        ]
        try:
            task.result = str(self.chat(messages) or "").strip()
            task.ok = bool(task.result)
            if not task.ok:
                task.error = "empty response from specialist"
        except Exception as exc:
            task.error = f"{type(exc).__name__}: {exc}"
            task.ok = False
        return task

    def run(self, goal: str, agents: list[str] | None = None) -> TeamResult:
        subtasks = self.plan(goal, agents)
        if not subtasks:
            return TeamResult(goal=goal, ok=False, summary="",
                              conflicts=["no specialist agents available"])

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            completed = list(pool.map(self._run_one, subtasks))

        ok_tasks = [t for t in completed if t.ok]
        result = TeamResult(goal=goal, subtasks=completed)
        result.conflicts = self._detect_conflicts(ok_tasks)
        result.summary = self._merge(goal, ok_tasks, completed)
        result.ok = bool(ok_tasks)
        return result

    # -------------------------------------------------------------- merging
    def _merge(self, goal: str, ok_tasks: list[SubTask],
               all_tasks: list[SubTask]) -> str:
        if not ok_tasks:
            errors = "; ".join(f"{t.agent}: {t.error}" for t in all_tasks)
            return f"No specialist produced a result ({errors})"
        blocks = [f"[{t.agent}] {t.result}" for t in ok_tasks]
        failed = [t for t in all_tasks if not t.ok]
        if failed:
            blocks.append(
                "Failed specialists: " + ", ".join(f"{t.agent} ({t.error})" for t in failed)
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _detect_conflicts(tasks: list[SubTask]) -> list[str]:
        """Flag specialists whose verdicts are mutually exclusive."""
        verdicts: dict[str, str] = {}
        for task in tasks:
            lowered = task.result.lower()
            if any(w in lowered for w in ("cannot", "impossible", "not possible",
                                          "unsafe", "拒绝", "不可行")):
                verdicts[task.agent] = "negative"
            elif any(w in lowered for w in ("可以", "feasible", "possible",
                                            "approved", "可行")):
                verdicts[task.agent] = "positive"
        kinds = set(verdicts.values())
        if {"positive", "negative"} <= kinds:
            return [
                "conflicting verdicts: "
                + ", ".join(f"{a}={v}" for a, v in sorted(verdicts.items()))
            ]
        return []
