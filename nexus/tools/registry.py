"""Tool registration, permission checks and invocation.

Every call goes through: permission check -> sandboxed execution -> structured
result. A failing tool returns an error result instead of raising, so a broken
plugin can never take down the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from nexus.tools.sandbox import Sandbox, SandboxViolation


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str = ""
    tool: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "output": self.output, "error": self.error,
                "tool": self.tool}


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    # Permission scope required to call it, checked against the grant list.
    scope: str = "read"
    # Schema hint for LLM tool-calling hosts.
    parameters: dict = field(default_factory=dict)
    dangerous: bool = False


class ToolRegistry:
    def __init__(self, sandbox: Sandbox | None = None,
                 allowed_scopes: set[str] | None = None):
        self.sandbox = sandbox or Sandbox("data/sandbox")
        self.allowed_scopes = allowed_scopes if allowed_scopes is not None else {
            "read", "write", "compute"
        }
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------- registry
    def register(self, spec: ToolSpec, *, replace: bool = False) -> None:
        if not spec.name or not callable(spec.handler):
            raise ValueError("tool needs a name and a callable handler")
        if spec.name in self._tools and not replace:
            raise ValueError(f"tool {spec.name!r} already registered")
        # Dangerous tools may be *declared* without the scope being granted -
        # they simply stay uncallable until the operator widens allowed_scopes.
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "scope": t.scope,
             "parameters": t.parameters,
             "callable": t.scope in self.allowed_scopes}
            for t in self._tools.values()
        ]

    def grant(self, scope: str) -> None:
        self.allowed_scopes = set(self.allowed_scopes) | {scope}

    def revoke(self, scope: str) -> None:
        self.allowed_scopes = set(self.allowed_scopes) - {scope}

    # ---------------------------------------------------------- invocation
    def call(self, name: str, /, **kwargs) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, error=f"unknown tool {name!r}", tool=name)
        if spec.scope not in self.allowed_scopes:
            return ToolResult(
                False,
                error=f"tool {name!r} requires scope {spec.scope!r} which is not granted",
                tool=name,
            )
        try:
            output = spec.handler(**kwargs)
            return ToolResult(True, output=output, tool=name)
        except SandboxViolation as exc:
            return ToolResult(False, error=f"sandbox denied: {exc}", tool=name)
        except TypeError as exc:
            return ToolResult(False, error=f"bad arguments: {exc}", tool=name)
        except Exception as exc:  # a tool must never crash the engine
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}", tool=name)


# --------------------------------------------------------------- built-ins
def build_default_registry(sandbox: Sandbox | None = None,
                           allowed_scopes: set[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry(sandbox, allowed_scopes)
    sb = registry.sandbox

    registry.register(ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file from the sandbox.",
        scope="read",
        parameters={"path": "str"},
        handler=lambda path: sb.read_text(path),
    ))
    registry.register(ToolSpec(
        name="write_file",
        description="Write UTF-8 text to a file inside the sandbox.",
        scope="write",
        parameters={"path": "str", "content": "str"},
        handler=lambda path, content: str(sb.write_text(path, content)),
    ))
    registry.register(ToolSpec(
        name="list_dir",
        description="List entries of a directory inside the sandbox.",
        scope="read",
        parameters={"path": "str"},
        handler=lambda path=".": sb.list_dir(path),
    ))
    registry.register(ToolSpec(
        name="python_eval",
        description="Evaluate a restricted arithmetic/string Python expression.",
        scope="compute",
        parameters={"expression": "str"},
        dangerous=True,
        handler=_safe_eval,
    ))
    registry.register(ToolSpec(
        name="run_shell",
        description="Run a shell command inside the sandbox (opt-in).",
        scope="shell",
        parameters={"command": "str"},
        dangerous=True,
        handler=lambda command: _run_shell(sb, command),
    ))
    return registry


_ALLOWED_NODES = {
    "Expression", "BinOp", "UnaryOp", "Constant", "Name", "Load",
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
    "USub", "UAdd", "Compare", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE",
    "BoolOp", "And", "Or", "Not", "IfExp", "Call", "List", "Tuple", "Dict",
}

# Functions a calculation is allowed to reach. Anything else - and every
# attribute access - stays out of reach, so an expression cannot walk
# ``().__class__.__bases__`` into the interpreter and call os.system().
_SAFE_CALLABLES = {
    "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
    "len": len, "int": int, "float": float, "str": str, "bool": bool,
    "sorted": sorted, "pow": pow, "divmod": divmod,
}


def _safe_eval(expression: str) -> Any:
    """Evaluate a pure arithmetic/string expression, nothing more.

    The AST whitelist alone is not a boundary: allowing ``Attribute`` plus
    ``Call`` lets ``().__class__.__base__.__subclasses__()`` return real
    classes, and one more subscript reaches a frame's globals - i.e. arbitrary
    code execution. Attribute access is therefore banned outright, calls are
    limited to a fixed set of builtins, and dunder names are rejected.
    """
    import ast

    if len(expression) > 2_000:
        raise ValueError("expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc}") from exc
    for node in ast.walk(tree):
        if type(node).__name__ not in _ALLOWED_NODES:
            raise ValueError(
                f"expression uses disallowed construct {type(node).__name__}"
            )
        if isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id not in _SAFE_CALLABLES and \
                    not _is_constant_name(node.id):
                raise ValueError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLABLES:
                raise ValueError("expression may only call a few builtins")
            if node.keywords:
                raise ValueError("keyword arguments are not supported")
    return eval(compile(tree, "<nexus-eval>", "eval"),
                {"__builtins__": {}}, dict(_SAFE_CALLABLES))


def _is_constant_name(name: str) -> bool:
    return name in {"True", "False", "None"}


def _run_shell(sandbox: Sandbox, command: str) -> dict:
    import subprocess

    sandbox.check_command(command)
    proc = subprocess.run(
        command, shell=True, cwd=sandbox.root, capture_output=True,
        text=True, timeout=30,
    )
    return {"returncode": proc.returncode, "stdout": proc.stdout[:10_000],
            "stderr": proc.stderr[:4_000]}
