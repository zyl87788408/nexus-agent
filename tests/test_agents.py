"""Multi-agent coordination: planning, concurrency, failure isolation, conflicts."""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.agents.base import DEFAULT_PROFILES, AgentProfile
from nexus.orchestration.coordinator import Coordinator, SubTask


def responder(mapping):
    """Build a chat function that answers per specialist role."""
    def chat(messages):
        system = messages[0]["content"]
        for marker, answer in mapping.items():
            if marker in system:
                return answer
        return "generic answer"
    return chat


class PlanningTest(unittest.TestCase):
    def test_plan_creates_one_task_per_specialist(self):
        coordinator = Coordinator(lambda m: "ok")
        tasks = coordinator.plan("do something")
        self.assertEqual(len(tasks), len(DEFAULT_PROFILES))
        self.assertEqual({t.agent for t in tasks}, set(DEFAULT_PROFILES))

    def test_plan_honours_explicit_agent_list(self):
        coordinator = Coordinator(lambda m: "ok")
        tasks = coordinator.plan("do something", agents=["researcher"])
        self.assertEqual([t.agent for t in tasks], ["researcher"])

    def test_unknown_agent_is_ignored(self):
        coordinator = Coordinator(lambda m: "ok")
        self.assertEqual(coordinator.plan("x", agents=["ghost"]), [])

    def test_custom_profiles_are_used(self):
        profiles = {"solo": AgentProfile("solo", "solo", "you are solo")}
        coordinator = Coordinator(lambda m: "answer", profiles=profiles)
        result = coordinator.run("goal")
        self.assertEqual(len(result.subtasks), 1)
        self.assertEqual(result.subtasks[0].agent, "solo")


class CollaborationTest(unittest.TestCase):
    def test_all_specialists_contribute(self):
        chat = responder({
            "research specialist": "研究结论 A",
            "engineering specialist": "实现方案 B",
            "review specialist": "评审意见 C",
        })
        result = Coordinator(chat, max_parallel=1).run("目标")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.subtasks), 3)
        for token in ("研究结论 A", "实现方案 B", "评审意见 C"):
            self.assertIn(token, result.summary)

    def test_empty_agent_answer_marks_failure(self):
        result = Coordinator(lambda m: "   ", max_parallel=1).run("目标")
        self.assertFalse(result.ok)
        self.assertTrue(all(not t.ok for t in result.subtasks))
        self.assertIn("No specialist produced a result", result.summary)


class FailureIsolationTest(unittest.TestCase):
    def test_one_crashed_specialist_does_not_stop_others(self):
        def chat(messages):
            if "research specialist" in messages[0]["content"]:
                raise RuntimeError("backend timeout")
            return "fine"

        result = Coordinator(chat, max_parallel=2).run("目标")
        self.assertTrue(result.ok)
        failed = [t for t in result.subtasks if not t.ok]
        self.assertEqual(len(failed), 1)
        self.assertIn("RuntimeError", failed[0].error)
        self.assertIn("Failed specialists", result.summary)

    def test_all_failed_reports_not_ok(self):
        def chat(messages):
            raise RuntimeError("down")

        result = Coordinator(chat, max_parallel=1).run("目标")
        self.assertFalse(result.ok)


class ConcurrencyTest(unittest.TestCase):
    def _measure_peak(self, limit):
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def chat(messages):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return "ok"

        Coordinator(chat, max_parallel=limit).run("并发测试")
        return active["peak"]

    def test_parallelism_never_exceeds_limit(self):
        for limit in (1, 2):
            peak = self._measure_peak(limit)
            self.assertLessEqual(peak, limit, f"peak {peak} exceeded {limit}")

    def test_limit_of_one_serialises(self):
        self.assertEqual(self._measure_peak(1), 1)

    def test_invalid_parallelism_falls_back_to_one(self):
        self.assertEqual(Coordinator(lambda m: "ok", max_parallel=0).max_parallel, 1)
        self.assertEqual(Coordinator(lambda m: "ok", max_parallel=-5).max_parallel, 1)


class ConflictTest(unittest.TestCase):
    def test_opposing_verdicts_are_flagged(self):
        tasks = [
            SubTask(goal="g", agent="a", result="the operation is feasible", ok=True),
            SubTask(goal="g", agent="b", result="this is impossible", ok=True),
        ]
        conflicts = Coordinator._detect_conflicts(tasks)
        self.assertTrue(conflicts)

    def test_chinese_verdicts_are_flagged(self):
        tasks = [
            SubTask(goal="g", agent="a", result="该方案可行", ok=True),
            SubTask(goal="g", agent="b", result="该方案不可行", ok=True),
        ]
        self.assertTrue(Coordinator._detect_conflicts(tasks))

    def test_agreement_is_not_a_conflict(self):
        tasks = [
            SubTask(goal="g", agent="a", result="feasible", ok=True),
            SubTask(goal="g", agent="b", result="also feasible", ok=True),
        ]
        self.assertEqual(Coordinator._detect_conflicts(tasks), [])

    def test_neutral_output_is_not_a_conflict(self):
        tasks = [SubTask(goal="g", agent="a", result="here are the details", ok=True)]
        self.assertEqual(Coordinator._detect_conflicts(tasks), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
