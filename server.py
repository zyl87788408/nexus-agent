"""OpenAI-compatible HTTP server for NEXUS Agent.

Exposes POST /v1/chat/completions (and /v1/models) so any OpenAI client
(OpenHands, LangChain, the openai SDK, ...) can use NEXUS as a backend.
Falls back to SimulatedBackend when no local model is reachable.
"""

import json
import os
import sys
import time
import uuid

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nexus.core.runtime import NexusRuntime

API_KEY = os.environ.get("NEXUS_API_KEY", "")

app = FastAPI(
    title="NEXUS Agent",
    description="Lightweight local AI engine plugin with an OpenAI-compatible API.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

runtime = NexusRuntime()


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


def _error(status_code, message, err_type="invalid_request_error", code=None):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": code}},
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "nexus-agent"}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "nexus-local",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "nexus",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
):
    if API_KEY and authorization != f"Bearer {API_KEY}":
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")

    if not request.messages:
        return _error(400, "'messages' must not be empty")

    messages = [m.model_dump(exclude_none=True) for m in request.messages]

    try:
        text, model_used = runtime.chat(messages)
    except Exception as exc:  # pragma: no cover - defensive
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
            "completion_tokens": len(text) // 4,
            "total_tokens": prompt_tokens + len(text) // 4,
        },
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8085)
