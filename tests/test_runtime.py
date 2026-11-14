import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.core.execution_loop import run_task as run_execution_loop
from nexus.core.lifecycle import Task, TaskStatus
from nexus.core.runtime import BACKENDS, NexusRuntime, SimulatedBackend
from nexus.models.llamacpp import LlamaCppBackend
from nexus.models.ollama import OllamaBackend


class FakeSelector:
    def __init__(self, candidates):
        self._candidates = candidates

    def select(self, context_tokens=2048):
        return {"candidates": self._candidates}


class ManyStepPlanner:
    def __init__(self, count):
        self.count = count

    def create_plan(self, goal):
        return [f"步骤 {i}" for i in range(1, self.count + 1)]


def make_runtime(candidates):
    runtime = NexusRuntime()
    runtime.selector = FakeSelector(candidates)
    return runtime


class WiringTest(unittest.TestCase):
    def test_known_backends_are_mapped(self):
        self.assertIs(BACKENDS["ollama"], OllamaBackend)
        self.assertIs(BACKENDS["llama.cpp"], LlamaCppBackend)

    def test_planner_shares_runtime_gateway(self):
        runtime = NexusRuntime()
        self.assertIs(runtime.planner.gateway, runtime.gateway)


class SimulatedBackendTest(unittest.TestCase):
    def test_returns_valid_step_list(self):
        plan = json.loads(SimulatedBackend().generate("Goal: 写一个排序函数"))
        self.assertTrue(plan["steps"])
        self.assertTrue(all(isinstance(s, str) and s.strip() for s in plan["steps"]))


class RuntimeFallbackTest(unittest.TestCase):
    """No backend means no real work.

    These tests previously asserted `completed` while nothing was executed.
    That is precisely the false-success path the engine must never take, so
    the contract is now: fall back to the simulated backend for *chat*, but
    fail the *task* with an explicit error.
    """

    def test_no_model_fails_with_explicit_error(self):
        runtime = make_runtime([])
        result = runtime.run_task("测试核心闭环")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["simulated"])
        self.assertIn("refusing to fake execution", result["error"])
        self.assertIsInstance(runtime.gateway.backend, SimulatedBackend)

    def test_unreachable_backend_fails_and_names_the_model(self):
        runtime = make_runtime([{"backend": "ollama", "name": "qwen2.5:3b"}])
        result = runtime.run_task("后端不可达时应如实失败")
        self.assertEqual(result["status"], "failed")
        # Report the backend that actually failed, not a simulated stand-in.
        self.assertEqual(result["model"], "qwen2.5:3b")
        self.assertTrue(result["error"])

    def test_unreachable_backend_still_answers_chat(self):
        # Chat keeps the helpful simulated fallback; only tasks refuse to fake.
        runtime = make_runtime([{"backend": "ollama", "name": "qwen2.5:3b"}])
        text, model = runtime.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(model, "simulated_model")
        self.assertIn("[simulated]", text)

    def test_unknown_backend_falls_back_to_simulated(self):
        runtime = make_runtime([{"backend": "mystery", "name": "x-model"}])
        result = runtime.run_task("未知后端")
        self.assertEqual(result["status"], "failed")
        self.assertIsInstance(runtime.gateway.backend, SimulatedBackend)

    def test_step_executor_marks_simulated(self):
        runtime = make_runtime([])
        step = runtime._step_executor(lambda: True)("任意步骤", 1)
        self.assertTrue(step["simulated"])
        self.assertFalse(step["done"])


class NoFallbackOnLogicErrorTest(unittest.TestCase):
    def test_invalid_plan_is_not_masked_by_fallback(self):
        class BadBackend(SimulatedBackend):
            def generate(self, prompt):
                return "not json"

        runtime = make_runtime([{"backend": "ollama", "name": "qwen2.5:3b"}])
        runtime.gateway.set_backend(BadBackend())
        result = runtime.run_task("计划非法时不应假装成功")
        self.assertEqual(result["status"], "failed")
        # A bad plan is a real failure; it must not be relabelled as a
        # simulated success. The fake selector reports no candidates, so the
        # runtime has no verified local model to name.
        self.assertTrue(result["error"])
        self.assertNotEqual(result["status"], "completed")


class ExecutionLoopTest(unittest.TestCase):
    def test_stops_at_max_steps_and_fails(self):
        task = Task(goal="无限步骤", max_steps=2)
        result = run_execution_loop(
            task, lambda s, i: {"done": False, "output": f"did {s}"},
            planner=ManyStepPlanner(5)
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(task.steps, 2)
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_done_with_output_completes_task(self):
        task = Task(goal="一步完成")
        result = run_execution_loop(
            task, lambda s, i: {"done": True, "output": "real result"}
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_done_without_evidence_fails(self):
        """A bare `done: True` is not proof of work and must not complete."""
        task = Task(goal="空转一步", max_retries=0)
        result = run_execution_loop(task, lambda s, i: {"done": True})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(task.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
