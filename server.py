"""OpenAI-compatible HTTP server for NEXUS.

Security defaults:
  * binds to 127.0.0.1 (never 0.0.0.0) unless explicitly overridden;
  * requires an API key whenever one is configured, and *refuses to start*
    on a non-loopback host without one;
  * CORS is an allow-list, not a wildcard.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexus.config import Config, Mode
from nexus.core.runtime import NexusRuntime
from nexus.adapters.loader import PluginLoadError
from nexus.net import CloudBlockedError

config = Config.load()
runtime = NexusRuntime(config)

API_KEY = config.server_api_key()

app = FastAPI(
    title="NEXUS Agent",
    description="Lightweight local AI engine plugin with an OpenAI-compatible API.",
    version="2.0.0",
)

_cors = config.get("server.cors_origins") or []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "nexus-local"
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    stop: str | list | None = None
    n: int = 1


class ModeRequest(BaseModel):
    mode: str
    allow_cloud: bool | None = None
    persist: bool = True


class TaskRequest(BaseModel):
    goal: str
    task_id: str | None = None
    resume_task_id: str | None = None


class TeamRequest(BaseModel):
    goal: str
    agents: list[str] | None = None


class MemoryRequest(BaseModel):
    content: str
    kind: str = "note"
    tags: list[str] | None = None
    importance: float = 0.5


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict = {}


def _error(status_code: int, message: str, err_type="invalid_request_error",
           code=None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": code}},
    )


def _auth_required() -> bool:
    """Evaluated per request so a key set after import takes effect."""
    return bool(API_KEY) or bool(config.get("server.require_auth", False))


def _authorized(authorization: str | None) -> bool:
    if not _auth_required():
        return True
    if not authorization:
        return False
    token = authorization.replace("Bearer ", "", 1).strip()
    return bool(API_KEY) and token == API_KEY


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": "nexus-agent", "version": "2.0.0"}


@app.get("/api/status")
def status():
    return runtime.status()


@app.get("/api/modes")
def modes():
    return {
        "modes": [m.value for m in Mode],
        "current": config.mode.value,
        "cloud_allowed": config.allow_cloud,
        "cloud_enabled": config.cloud_enabled,
    }


@app.post("/api/mode")
def set_mode(request: ModeRequest, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    try:
        return runtime.set_mode(request.mode, allow_cloud=request.allow_cloud,
                                persist=request.persist)
    except ValueError as exc:
        return _error(400, str(exc))


@app.post("/api/task")
def run_task(request: TaskRequest, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    if not request.goal.strip():
        return _error(400, "'goal' must not be empty")
    return runtime.run_task(
        request.goal,
        task_id=request.task_id,
        resume_task_id=request.resume_task_id,
    )


@app.post("/api/task/{task_id}/interrupt")
def interrupt_task(task_id: str, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    if not runtime.interrupt(task_id):
        return _error(404, f"no running task '{task_id}'", "not_found_error")
    return {"interrupted": True, "task_id": task_id}


@app.get("/api/tasks")
def list_tasks():
    return {"running": runtime.active_tasks()}


@app.post("/api/team")
def run_team(request: TeamRequest, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    if not request.goal.strip():
        return _error(400, "'goal' must not be empty")
    try:
        return runtime.run_team(request.goal, request.agents)
    except ValueError as exc:
        # e.g. an unknown specialist name
        return _error(400, str(exc))


@app.get("/api/tools")
def list_tools():
    return {"tools": runtime.tools.specs()}


@app.post("/api/tool")
def call_tool(request: ToolCallRequest,
              authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    result = runtime.tools.call(request.tool, **request.arguments)
    return result.to_dict()


@app.get("/api/memory")
def memory_recent(limit: int = 20, kind: str | None = None,
                  authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    return {"memories": runtime.memory.recent(limit, kind),
            "count": runtime.memory.count()}


@app.get("/api/memory/search")
def memory_search(q: str, limit: int = 10,
                  authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    return {"query": q, "results": runtime.memory.search(q, limit=limit)}


@app.post("/api/memory")
def memory_add(request: MemoryRequest,
               authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    try:
        memory_id = runtime.memory.add(
            request.content, kind=request.kind, tags=request.tags,
            importance=request.importance,
        )
    except ValueError as exc:
        return _error(400, str(exc))
    return {"id": memory_id, "stored": True}


@app.delete("/api/memory/{memory_id}")
def memory_delete(memory_id: str,
                  authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    return {"deleted": runtime.memory.delete(memory_id)}


@app.get("/api/plugins")
def plugins():
    return runtime.plugins.status()


class PluginStateRequest(BaseModel):
    enabled: bool


@app.post("/api/plugins/{name}/enable")
def enable_plugin(name: str, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    try:
        return runtime.plugins.enable(name)
    except PluginLoadError as exc:
        return _error(404, str(exc), "not_found_error")


@app.post("/api/plugins/{name}/disable")
def disable_plugin(name: str, authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    try:
        return runtime.plugins.disable(name)
    except PluginLoadError as exc:
        return _error(404, str(exc), "not_found_error")


@app.post("/api/plugins/{name}")
def set_plugin_state(name: str, request: PluginStateRequest,
                     authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    try:
        return (runtime.plugins.enable(name) if request.enabled
                else runtime.plugins.disable(name))
    except PluginLoadError as exc:
        return _error(404, str(exc), "not_found_error")


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "nexus-local", "object": "model",
             "created": int(time.time()), "owned_by": "nexus"}
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest,
                     authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    if not request.messages:
        return _error(400, "'messages' must not be empty")

    messages = [m.model_dump(exclude_none=True) for m in request.messages]

    try:
        text, model_used, source = runtime.chat_detail(messages)
    except CloudBlockedError as exc:
        return _error(403, str(exc), "permission_error", "cloud_blocked")
    except Exception as exc:  # defensive: never leak a stack trace to clients
        print(f"[nexus] chat failed: {exc}", file=sys.stderr)
        return _error(500, str(exc), "server_error")

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if request.stream:
        return StreamingResponse(
            _stream_chunks(completion_id, created, model_used, text),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    prompt_tokens = sum(len(str(m.get("content") or "")) for m in messages) // 4
    completion_tokens = len(text) // 4
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_used,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "nexus": {"source": source, "simulated": source == "simulated"},
    }


def _chunk(completion_id, created, model, delta, finish_reason=None):
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_chunks(completion_id, created, model, text):
    yield _chunk(completion_id, created, model, {"role": "assistant", "content": ""})
    for line in text.splitlines(keepends=True):
        yield _chunk(completion_id, created, model, {"content": line})
    yield _chunk(completion_id, created, model, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"


def main() -> int:
    import uvicorn

    host = config.get("server.host", "127.0.0.1")
    port = int(config.get("server.port", 8085))

    # Refuse to expose an unauthenticated service on a public interface.
    if host not in {"127.0.0.1", "localhost", "::1"} and not API_KEY:
        print(
            f"ERROR: refusing to bind {host} without NEXUS_API_KEY set.\n"
            "Set the API key, or bind 127.0.0.1 for local-only use.",
            file=sys.stderr,
        )
        return 2

    print(f"NEXUS listening on http://{host}:{port} (mode={config.mode.value}, "
          f"auth={'on' if API_KEY else 'off'})")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
