import json
import sys
from pathlib import Path

# 允许 `python nexus/core/runtime.py` 直接运行（脚本模式下项目根不在 sys.path 中）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nexus.core.execution_loop import run_task as run_execution_loop
from nexus.core.lifecycle import Task
from nexus.models.gateway import ModelBackend, ModelGateway
from nexus.models.llamacpp import LlamaCppBackend
from nexus.models.ollama import OllamaBackend
from nexus.models.selector import ModelSelector
from nexus.planner.planner import Planner


class SimulatedBackend(ModelBackend):
    """离线兜底后端：没有本地模型时也能跑通「规划 + 执行」闭环。"""

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
            "[simulated] NEXUS 当前未检测到可用的本地模型，正在以模拟模式应答。\n"
            f"收到的问题：{user_text}\n"
            "模拟执行计划：\n"
            "1. 分析任务目标\n"
            "2. 拆解为可执行子步骤\n"
            "3. 汇总执行结果并输出结论\n"
            "配置 Ollama 或 llama.cpp 后可获得真实模型输出。"
        )


def _content_text(message):
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


def _last_user_content(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_text(message)
    return ""


def _build_prompt(messages):
    return "\n".join(
        f"{message.get('role', 'user')}: {_content_text(message)}"
        for message in messages
    )


BACKENDS = {
    "ollama": OllamaBackend,
    "llama.cpp": LlamaCppBackend,
}

# 仅在这类错误上回落模拟模式，避免把「计划非法」等真实缺陷掩盖成成功
NETWORK_ERRORS = {"URLError", "HTTPError", "ConnectionError", "TimeoutError", "OSError"}


class NexusRuntime:
    def __init__(self):
        self.selector = ModelSelector()
        self.gateway = ModelGateway()
        self.planner = Planner(self.gateway)
        self._simulated = None

    def run_task(self, task_goal):
        print(f"🚀 [NEXUS] 接收到任务目标: {task_goal}")

        # 1. 发现并选择本地模型
        target_model, simulated = self._resolve_backend()

        # 2 + 3. 规划器拆解任务，执行引擎逐步执行（create_plan 由执行循环调用）
        print("🧠 [Planner] 正在拆解任务...")
        print("⚙️ [Runtime] 开始执行计划...")
        result = run_execution_loop(
            Task(goal=task_goal),
            self._step_executor(lambda: simulated),
            planner=self.planner,
        )

        # 本地后端不可达时，回落到模拟模型重跑一遍，保证闭环不中断
        if result.get("error_type") in NETWORK_ERRORS and not simulated:
            print(f"⚠️ 警告: 模型 {target_model} 不可用（{result.get('error')}），回退模拟模式。")
            self._simulated_backend()
            simulated = True
            target_model = "simulated_model"
            result = run_execution_loop(
                Task(goal=task_goal),
                self._step_executor(lambda: simulated),
                planner=self.planner,
            )

        result["model"] = target_model

        if result.get("status") == "completed":
            print(
                f"🎉 [NEXUS] 任务执行完成！共执行 {result.get('steps', 0)} 个步骤"
                f"（模型: {target_model}）"
            )
        else:
            print(f"❌ [NEXUS] 任务未完成: {result.get('error') or result.get('status')}")

        return result

    def chat(self, messages):
        """按 OpenAI 对话格式处理一次请求，返回 (文本, 模型名)。"""
        target_model, simulated = self._resolve_backend()
        backend = self.gateway.backend

        if not simulated and hasattr(backend, "chat"):
            try:
                return backend.chat(messages), target_model
            except Exception as exc:
                if type(exc).__name__ not in NETWORK_ERRORS:
                    raise
                print(f"⚠️ 警告: 模型 {target_model} 不可用（{exc}），回退模拟模式。")

        return self._simulated_backend().chat(messages), "simulated_model"

    def _simulated_backend(self):
        self._simulated = SimulatedBackend()
        self.gateway.set_backend(self._simulated)
        return self._simulated

    def _resolve_backend(self):
        backend = self.gateway.backend
        # 外部注入的后端优先；只有本次兜底装的模拟后端才需要重新探测，
        # 这样模型上线后能自动恢复，同时不覆盖调用方自己注入的后端。
        if backend is not None and backend is not self._simulated:
            return getattr(backend, "model", None) or "injected_model", False

        candidates = self.selector.select().get("candidates") or []
        if not candidates:
            print("⚠️ 警告: 未发现可用本地模型。将使用模拟模式或 API 兜底。")
            self._simulated_backend()
            return "simulated_model", True

        model = candidates[0]
        target_model = model["name"]
        backend_cls = BACKENDS.get(model.get("backend"))
        if backend_cls is None:
            print(f"⚠️ 警告: 后端 {model.get('backend')!r} 未实现，回退模拟模式。")
            self._simulated_backend()
            return target_model, True

        self._simulated = None
        self.gateway.set_backend(backend_cls(target_model))
        print(f"✅ 选定模型: {target_model}")
        return target_model, False

    def _step_executor(self, get_simulated):
        def execute_step(step, index):
            simulated = get_simulated()
            label = "模拟执行" if simulated else "执行"
            print(f"   ↳ [{label}] 步骤 {index}: {step}")
            return {"step": index, "goal": step, "simulated": simulated, "done": False}

        return execute_step


if __name__ == "__main__":
    # 启动测试
    runtime = NexusRuntime()
    outcome = runtime.run_task("测试一下系统的核心闭环")
    raise SystemExit(0 if outcome.get("status") == "completed" else 1)
