import json
import threading
import sys
from pathlib import Path

# Allow `python nexus/core/runtime.py` (script mode puts no project root on sys.path)
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nexus.adapters.manager import PluginManager
from nexus.config import Config, Mode
from nexus.core.execution_loop import run_task as run_execution_loop
from nexus.core.journal import Checkpoint, Journal
from nexus.core.lifecycle import Task
from nexus.memory.store import MemoryStore
from nexus.models.cloud import CloudBackend
from nexus.models.gateway import ModelBackend, ModelGateway
from nexus.models.llamacpp import LlamaCppBackend
from nexus.models.ollama import OllamaBackend
from nexus.models.selector import ModelSelector
from nexus.net import CloudBlockedError
from nexus.net import NetworkPolicy
from nexus.orchestration.coordinator import Coordinator
from nexus.planner.planner import Planner
from nexus.tools.registry import build_default_registry
from nexus.tools.sandbox import Sandbox

BACKENDS = {
    "ollama": OllamaBackend,
    "llama.cpp": LlamaCppBackend,
    "cloud": CloudBackend,
}

# Fall back only on transport errors, never on logic errors such as a bad plan.
NETWORK_ERRORS = (
    "NetworkError",
    "URLError",
    "HTTPError",
    "ConnectionError",
    "TimeoutError",
    "OSError",
)


class SimulatedBackend(ModelBackend):
    """Last-resort backend so the engine always boots.

    It is explicitly labelled: callers can see `simulated=True` and must never
    treat its output as real model capability.
    """

    model = "simulated_model"

    def generate(self, prompt: str) -> str:
        goal = prompt.rsplit("Goal:", 1)[-1].strip() or "unknown task"
        return json.dumps(
            {
                "steps": [
                    f"分析任务目标：{goal}",
                    "拆解为可执行子步骤",
                    "汇总执行结果并输出结论",
                ]
            },
            ensure_ascii=False,
        )

    def chat(self, messages: list[dict]) -> str:
        user_text = _last_user_content(messages)
        return (
            "[simulated] NEXUS 未检测到可用的推理后端。\n"
            f"收到的问题：{user_text}\n"
            "这是兜底回答，不是模型推理结果。\n"
            "请启动 Ollama/llama.cpp，或在配置中启用云端供应商。"
        )


def _content_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _last_user_content(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_text(message)
    return ""


def _build_prompt(messages: list[dict]) -> str:
    return "\n".join(
        f"{m.get('role', 'user')}: {_content_text(m)}" for m in messages
    )


class NexusRuntime:
    """Wires configuration, models, tools, memory, plugins and agents together."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self.selector = ModelSelector()
        self.gateway = ModelGateway()
        self.planner = Planner(self.gateway)
        self._simulated = None
        self._active_model = None
        self._active_source = None
        # Live tasks, addressable by id so an interrupt can reach a running one.
        self._active_tasks: dict[str, Task] = {}
        self._tasks_lock = threading.Lock()

        self.policy = self._build_policy()

        self.tools = build_default_registry(
            Sandbox(self.config.resolve_path("data/sandbox"))
        )
        if self.config.get("tools.allow_shell"):
            # Shell commands are a second egress path: curl, git and python all
            # reach the network without passing through nexus/net.py. Allowing
            # both would make the offline guarantee untrue, so refuse instead of
            # silently running with a promise the engine cannot keep.
            if self.config.mode is Mode.OFFLINE:
                raise ValueError(
                    "tools.allow_shell cannot be combined with offline mode: "
                    "shell commands reach the network without passing through "
                    "nexus/net.py, which would break the offline guarantee"
                )
            self.tools.grant("shell")
            self.tools.sandbox.allow_shell = True

        self.memory = MemoryStore(
            self.config.resolve_path(self.config.get("memory.db_path", "data/memory.db"))
        )

        checkpoint_dir = self.config.resolve_path(
            self.config.get("execution.checkpoint_dir", "data/checkpoints")
        )
        self.checkpoint = Checkpoint(checkpoint_dir)
        self.journal_dir = checkpoint_dir

        self.plugins = PluginManager(
            self.config.resolve_path(self.config.get("plugins", "plugins")),
            services={"memory": self.memory, "tools": self.tools},
        )
        self.plugins.activate_all()

    # ----------------------------------------------------------------- policy
    def _build_policy(self) -> NetworkPolicy:
        return NetworkPolicy(self.config.mode, self.config.allow_cloud)

    def refresh_policy(self) -> NetworkPolicy:
        self.policy = self._build_policy()
        return self.policy

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "mode": self.config.mode.value,
            "active_model": self._active_model,
            "active_source": self._active_source,
            "cloud_allowed": self.config.allow_cloud,
            "cloud_enabled": self.config.cloud_enabled,
            "tools": self.tools.names(),
            "plugins": self.plugins.status(),
            "memory_entries": self.memory.count(),
            "simulated": isinstance(self.gateway.backend, SimulatedBackend),
        }

    # ----------------------------------------------------------------- models
    def set_mode(self, mode, *, allow_cloud: bool | None = None,
                 persist: bool = True) -> dict:
        """Switch mode, then prove the new setting actually works."""
        self.config.mode = Mode.parse(mode)
        if allow_cloud is not None:
            self.config.allow_cloud = allow_cloud
        self.refresh_policy()
        if persist:
            self.config.save()
        verification = self.verify_connection()
        return {
            "mode": self.config.mode.value,
            "verified": verification.get("ok", False),
            "detail": verification,
        }

    def verify_connection(self) -> dict:
        """Prove the configured backend is reachable before claiming success."""
        mode = self.config.mode
        if mode is Mode.OFFLINE:
            candidates = self._select(probe=True)["candidates"]
            if candidates:
                return {"ok": True, "source": "local", "model": candidates[0]["name"]}
            return {"ok": False, "source": "local",
                    "error": "no usable local model (offline mode)"}

        if mode is Mode.ONLINE:
            if not self.config.cloud_enabled:
                return {"ok": False, "source": "cloud",
                        "error": "cloud provider is not enabled in config"}
            if not self.config.cloud_api_key():
                return {"ok": False, "source": "cloud",
                        "error": "cloud API key environment variable is empty"}
            backend = self._cloud_backend()
            if backend is None:
                return {"ok": False, "source": "cloud",
                        "error": "cloud access is not authorized"}
            result = backend.probe()
            return {"ok": bool(result.get("available")), "source": "cloud",
                    "detail": result}

        # AUTO: prefer local; reach for cloud only when explicitly authorized.
        local = self._select(probe=True)["candidates"]
        if local:
            return {"ok": True, "source": "local", "model": local[0]["name"]}
        if self.config.cloud_enabled and self.config.allow_cloud:
            backend = self._cloud_backend()
            if backend is not None:
                result = backend.probe()
                return {"ok": bool(result.get("available")), "source": "cloud",
                        "detail": result}
        return {"ok": False, "source": "none",
                "error": "no local model and cloud is not authorized"}

    def _cloud_backend(self):
        if not self.config.allow_cloud:
            return None
        return CloudBackend(
            model=self.config.get("cloud.model"),
            base_url=self.config.get("cloud.base_url"),
            api_key=self.config.cloud_api_key(),
            policy=self.policy,
        )

    def _select(self, *, probe: bool = False) -> dict:
        """Call the selector while tolerating injected selectors without `probe`."""
        try:
            return self.selector.select(probe=probe)
        except TypeError:
            return self.selector.select()

    def _resolve_backend(self, *, probe: bool = False):
        """Choose a backend while honouring the offline guarantee."""
        # Any explicitly injected backend wins, including a simulated one: the
        # caller asked for it, so discovery must not override that choice.
        backend = self.gateway.backend
        if backend is not None:
            model = getattr(backend, "model", None) or "injected_model"
            return model, isinstance(backend, SimulatedBackend)

        mode = self.config.mode
        if mode is not Mode.ONLINE:
            selection = self._select(probe=probe)
            candidates = selection.get("candidates") or []
            if candidates:
                model = candidates[0]
                backend_cls = BACKENDS.get(model.get("backend"))
                if backend_cls is not None:
                    self._simulated = None
                    host = model.get("host")
                    kwargs = {"policy": self.policy}
                    if host:
                        kwargs["host"] = host
                    self.gateway.set_backend(backend_cls(model["name"], **kwargs))
                    self._active_source = "local"
                    return model["name"], False
            if mode is Mode.OFFLINE:
                # Never silently escalate to the cloud while offline.
                self._simulated_backend()
                self._active_source = "simulated"
                return "simulated_model", True

        if self.config.cloud_enabled and self.config.allow_cloud:
            cloud = self._cloud_backend()
            if cloud is not None:
                self._simulated = None
                self.gateway.set_backend(cloud)
                self._active_source = "cloud"
                return cloud.model, False

        self._simulated_backend()
        self._active_source = "simulated"
        return "simulated_model", True

    def _simulated_backend(self):
        self._simulated = SimulatedBackend()
        self.gateway.set_backend(self._simulated)
        return self._simulated

    # ------------------------------------------------------------------- chat
    def chat(self, messages: list[dict], *, use_memory: bool = True):
        """Handle one conversation. Returns (text, model) - the v1 contract."""
        text, model, _source = self.chat_detail(messages, use_memory=use_memory)
        return text, model

    def chat_detail(self, messages: list[dict], *, use_memory: bool = True):
        """Like chat() but also reports which source answered.

        Returns (text, model, source) where source is
        "local" | "cloud" | "simulated".
        """
        target_model, simulated = self._resolve_backend()
        self._active_model = target_model

        backend = self.gateway.backend
        if simulated:
            # Use the injected simulated backend when there is one, so a caller
            # that supplied its own is genuinely used rather than replaced.
            fallback = backend if isinstance(backend, SimulatedBackend) \
                else self._simulated_backend()
            return fallback.chat(messages), "simulated_model", "simulated"

        payload = list(messages)
        if use_memory:
            query = _last_user_content(messages)
            context = self.memory.context_for(query, limit=3) if query else ""
            if context:
                payload = [{"role": "system", "content": context}] + payload

        try:
            if hasattr(backend, "chat"):
                text = backend.chat(payload)
            else:
                text = backend.generate(_build_prompt(payload))
        except CloudBlockedError:
            # Policy violations are surfaced, never downgraded to a fake answer.
            self._simulated_backend()
            self._active_source = "simulated"
            raise
        except Exception as exc:
            if type(exc).__name__ not in NETWORK_ERRORS:
                raise
            print(f"⚠️  backend {target_model} unavailable ({exc}); using simulation")
            text = self._simulated_backend().chat(messages)
            self._active_model = "simulated_model"
            return text, "simulated_model", "simulated"

        self._active_source = "cloud" if isinstance(backend, CloudBackend) else "local"
        return text, target_model, self._active_source

    # ------------------------------------------------------------------ tasks
    def _step_executor(self, get_simulated):
        """Execute one plan step for real: ask the model, then verify the answer."""

        def execute_step(step: str, index: int) -> dict:
            if get_simulated():
                # Refuse to fabricate execution when there is no backend.
                return {
                    "step": index,
                    "goal": step,
                    "simulated": True,
                    "done": False,
                    "output": "",
                    "error": "no model backend available; refusing to fake execution",
                    "retryable": False,
                }
            backend = self.gateway.backend
            prompt = [
                {
                    "role": "system",
                    "content": "执行下面这一步，输出可直接使用的具体结果，不要复述计划。",
                },
                {"role": "user", "content": step},
            ]
            try:
                if hasattr(backend, "chat"):
                    output = backend.chat(prompt)
                else:
                    output = backend.generate(_build_prompt(prompt))
            except Exception as exc:
                return {
                    "step": index,
                    "goal": step,
                    "done": False,
                    "output": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "retryable": type(exc).__name__ in NETWORK_ERRORS,
                }
            text = (output or "").strip()
            return {
                "step": index,
                "goal": step,
                "simulated": False,
                "done": False,
                "output": text,
                "error": "" if text else "model returned an empty result",
                "retryable": True,
            }

        return execute_step

    @staticmethod
    def _verify_step(step: str, result: dict) -> tuple[bool, str]:
        """Independent check that a step produced usable, non-faked output."""
        output = str(result.get("output") or "").strip()
        if not output:
            return False, "empty output"
        if result.get("simulated"):
            return False, "simulated steps are never accepted as real work"
        if output == str(step).strip():
            return False, "output only repeats the instruction"
        return True, ""

    def run_task(self, task_goal: str, *, resume_task_id: str | None = None,
                 task_id: str | None = None) -> dict:
        target_model, simulated = self._resolve_backend(probe=True)
        self._active_model = target_model

        task = Task(goal=task_goal)
        if resume_task_id:
            task.task_id = resume_task_id
        elif task_id:
            task.task_id = task_id

        journal = Journal(self.journal_dir, task.task_id)
        planner = None if simulated else self.planner

        with self._tasks_lock:
            self._active_tasks[task.task_id] = task
        try:
            result = run_execution_loop(
                task,
                self._step_executor(lambda: simulated),
                planner=planner,
                verify=self._verify_step,
                max_retries=self.config.get("execution.max_retries", 2),
                journal=journal,
                checkpoint=self.checkpoint,
                resume=bool(resume_task_id),
            )
        finally:
            with self._tasks_lock:
                self._active_tasks.pop(task.task_id, None)

        result["model"] = "simulated_model" if simulated else target_model
        result["source"] = self._active_source
        result["task_id"] = task.task_id
        result["simulated"] = simulated
        result["journal"] = str(journal.path)
        return result

    def interrupt(self, task_id: str) -> bool:
        """Request cancellation of a running task. False if no such task."""
        with self._tasks_lock:
            task = self._active_tasks.get(task_id)
        if task is None:
            return False
        task.cancel()
        return True

    def active_tasks(self) -> list[str]:
        with self._tasks_lock:
            return sorted(self._active_tasks)

    def execute(self, goal: str, *, resume_task_id: str | None = None) -> dict:
        """Convenience wrapper with progress output."""
        print(f"🚀 [NEXUS] task: {goal}")
        result = self.run_task(goal, resume_task_id=resume_task_id)
        if result.get("status") == "completed":
            print(f"🎉 completed in {result['steps']} steps (model: {result['model']})")
        else:
            print(f"❌ not completed: {result.get('error') or result.get('status')}")
        return result

    # ------------------------------------------------------------- multi-agent
    def run_team(self, goal: str, agents: list[str] | None = None) -> dict:
        target_model, simulated = self._resolve_backend()
        self._active_model = target_model

        if simulated:
            return {
                "goal": goal,
                "ok": False,
                "simulated": True,
                "summary": "",
                "conflicts": ["no model backend available"],
                "subtasks": [],
            }

        plan = self._select().get("hardware_profile", {})
        parallel = max(1, int(plan.get("max_parallel_agents", 1)))

        def _chat(messages):
            return self.chat(messages, use_memory=False)[0]

        team = Coordinator(_chat, max_parallel=parallel).run(goal, agents)
        payload = team.to_dict()
        payload["model"] = target_model
        payload["source"] = self._active_source
        payload["parallelism"] = parallel
        return payload


if __name__ == "__main__":
    runtime = NexusRuntime()
    outcome = runtime.execute("测试一下系统的核心闭环")
    raise SystemExit(0 if outcome.get("status") == "completed" else 1)
