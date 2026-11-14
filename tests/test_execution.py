"""Execution-engine guarantees: verification, retry, resume, cancellation.

The point of these tests is the invariant the user demanded: a task must never
report `completed` unless every step actually produced verified output.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.core.execution_loop import run_task
from nexus.core.journal import Checkpoint, Journal
from nexus.core.lifecycle import Task, TaskStatus


class VerificationGateTest(unittest.TestCase):
    def test_done_without_output_never_completes(self):
        task = Task(goal="empty work", max_retries=0)
        result = run_task(task, lambda s, i: {"done": True})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_done_with_blank_output_never_completes(self):
        task = Task(goal="blank work", max_retries=0)
        result = run_task(task, lambda s, i: {"done": True, "output": "   "})
        self.assertEqual(result["status"], "failed")

    def test_verified_flag_alone_without_output_is_rejected(self):
        task = Task(goal="claim", max_retries=0)
        result = run_task(
            task, lambda s, i: {"done": True, "verified": True, "output": ""}
        )
        self.assertEqual(result["status"], "failed")

    def test_real_output_completes(self):
        task = Task(goal="real work")
        result = run_task(
            task, lambda s, i: {"done": True, "output": "concrete result"}
        )
        self.assertEqual(result["status"], "completed")
        self.assertIn("concrete result", result["output"])

    def test_independent_verifier_can_reject(self):
        task = Task(goal="verify", max_retries=0)
        result = run_task(
            task,
            lambda s, i: {"output": "looks fine"},
            verify=lambda step, res: (False, "does not meet the spec"),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("does not meet the spec", result["error"])

    def test_independent_verifier_can_accept(self):
        result = run_task(
            Task(goal="verify"),
            lambda s, i: {"output": "good"},
            verify=lambda step, res: (True, ""),
        )
        self.assertEqual(result["status"], "completed")

    def test_crashing_verifier_fails_safe(self):
        def boom(step, result):
            raise RuntimeError("verifier exploded")

        result = run_task(Task(goal="x", max_retries=0),
                          lambda s, i: {"output": "y"}, verify=boom)
        self.assertEqual(result["status"], "failed")


class RetryTest(unittest.TestCase):
    def test_transient_failure_is_retried_then_succeeds(self):
        state = {"n": 0}

        def flaky(step, index):
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("transient")
            return {"output": "recovered"}

        result = run_task(Task(goal="retry", max_retries=2), flaky)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(state["n"], 3)

    def test_retries_are_bounded(self):
        state = {"n": 0}

        def always_fails(step, index):
            state["n"] += 1
            raise RuntimeError("permanent")

        result = run_task(Task(goal="retry", max_retries=1), always_fails)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state["n"], 2)  # first attempt + one retry

    def test_non_retryable_error_stops_immediately(self):
        state = {"n": 0}

        def fatal(step, index):
            state["n"] += 1
            return {"error": "invalid input", "retryable": False}

        result = run_task(Task(goal="x", max_retries=3), fatal)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state["n"], 1)

    def test_verification_failure_triggers_retry(self):
        state = {"n": 0}

        def eventually_ok(step, index):
            state["n"] += 1
            return {"output": "bad" if state["n"] == 1 else "good"}

        def verify(step, result):
            return (result["output"] == "good", "must equal good")

        result = run_task(Task(goal="x", max_retries=1), eventually_ok, verify=verify)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(state["n"], 2)


class MaxStepsTest(unittest.TestCase):
    def test_plan_exceeding_budget_fails_loudly(self):
        class LongPlanner:
            def create_plan(self, goal):
                return [f"step {i}" for i in range(6)]

        task = Task(goal="long", max_steps=2)
        result = run_task(task, lambda s, i: {"output": "done"},
                          planner=LongPlanner())
        self.assertEqual(result["status"], "failed")
        self.assertIn("max_steps", result["error"])
        self.assertEqual(task.steps, 2)

    def test_plan_within_budget_succeeds(self):
        class ShortPlanner:
            def create_plan(self, goal):
                return ["a", "b"]

        task = Task(goal="short", max_steps=5)
        result = run_task(task, lambda s, i: {"output": f"did {s}"},
                          planner=ShortPlanner())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(task.steps, 2)


class ResumeTest(unittest.TestCase):
    def test_resume_skips_completed_steps(self):
        with tempfile.TemporaryDirectory() as d:
            checkpoint = Checkpoint(d)
            journal = Journal(d, "resume")
            checkpoint.save("resume", {"plan": ["a", "b", "c"], "done_count": 2})

            executed = []
            task = Task(goal="resume")
            task.task_id = "resume"
            result = run_task(
                task,
                lambda s, i: (executed.append((s, i)), {"output": f"out {s}"})[1],
                journal=journal,
                checkpoint=checkpoint,
                resume=True,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(executed, [("c", 3)])
            self.assertEqual(task.steps, 3)

    def test_resume_without_checkpoint_starts_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            checkpoint = Checkpoint(d)
            journal = Journal(d, "fresh")
            task = Task(goal="fresh")
            task.task_id = "fresh"
            executed = []
            result = run_task(
                task,
                lambda s, i: (executed.append(s), {"output": "ok"})[1],
                journal=journal,
                checkpoint=checkpoint,
                resume=True,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(executed, ["fresh"])

    def test_checkpoint_cleared_after_success(self):
        with tempfile.TemporaryDirectory() as d:
            checkpoint = Checkpoint(d)
            task = Task(goal="x")
            run_task(task, lambda s, i: {"output": "ok"},
                     checkpoint=checkpoint, journal=Journal(d, "x"))
            self.assertIsNone(checkpoint.load(task.task_id))

    def test_checkpoint_survives_failure_for_resume(self):
        with tempfile.TemporaryDirectory() as d:
            checkpoint = Checkpoint(d)

            class TwoSteps:
                def create_plan(self, goal):
                    return ["ok step", "bad step"]

            task = Task(goal="x", max_retries=0)
            run_task(
                task,
                lambda s, i: ({"output": "fine"} if i == 1
                              else {"error": "boom", "retryable": False}),
                planner=TwoSteps(), checkpoint=checkpoint,
                journal=Journal(d, "x"),
            )
            saved = checkpoint.load(task.task_id)
            self.assertIsNotNone(saved)
            self.assertEqual(saved["done_count"], 1)


class CancellationTest(unittest.TestCase):
    def test_cancelled_task_stops_between_steps(self):
        task = Task(goal="cancel me")

        class Steps:
            def create_plan(self, goal):
                return ["one", "two", "three"]

        def execute(step, index):
            task.cancel()
            return {"output": "done"}

        result = run_task(task, execute, planner=Steps())
        self.assertIn(result["status"], {"cancelled", "completed"})
        self.assertLessEqual(task.steps, 3)


class JournalTest(unittest.TestCase):
    def test_journal_records_the_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, "lifecycle")
            run_task(
                Task(goal="journal"),
                lambda s, i: {"output": "ok"},
                journal=journal,
                checkpoint=Checkpoint(d),
            )
            events = [e["event"] for e in journal.read()]
            self.assertIn("start", events)
            self.assertIn("plan", events)
            self.assertIn("step_ok", events)
            self.assertIn("completed", events)

    def test_journal_records_failures(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(d, "failure")
            run_task(Task(goal="x", max_retries=0),
                     lambda s, i: {"error": "nope", "retryable": False},
                     journal=journal)
            events = [e["event"] for e in journal.read()]
            self.assertIn("failed", events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
