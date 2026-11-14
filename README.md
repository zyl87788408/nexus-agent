# NEXUS Agent

> **A lightweight local AI engine plugin that speaks the OpenAI API — with built-in memory-safety estimation.**

NEXUS Agent is a small, dependency-light engine that exposes a **drop-in OpenAI-compatible
HTTP endpoint**. Any host engine that supports the OpenAI protocol — **OpenHands**,
**LangChain**, the official `openai` SDK, Continue, LM Studio-style clients, and so on —
can point at NEXUS and start chatting.

No cloud dependency. No GPU required. **If no local model is found, NEXUS automatically
falls back to `SimulatedBackend`, so the project runs successfully on any machine.**

---

## Features

| Feature | Description |
| --- | --- |
| **OpenAI-compatible API** | `POST /v1/chat/completions` with standard `chat.completion` JSON, plus SSE streaming (`stream: true`). |
| **Works out of the box** | No Ollama / llama.cpp required. A built-in simulated backend answers when no model is reachable. |
| **Automatic backend discovery** | Detects models via Ollama (`127.0.0.1:11434`) and llama.cpp (`127.0.0.1:8080`), then auto-selects. |
| **Built-in memory-safety estimation** | Estimates whether a model fits in available RAM *before* loading it — no more OOM crashes. |
| **Hardware-aware planning** | Detects RAM/CPU and adapts the execution profile (Lite / Balanced / Performance). |
| **Task planner + execution loop** | Decomposes a goal into steps and executes them with step limits and cancellation. |
| **Plugin subsystem** | Manifest validation, permission scopes, lifecycle management. |

---

## Quick start

```bash
git clone <your-repo-url> nexus-agent
cd nexus-agent

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python server.py                   # serves on http://127.0.0.1:8085
```

You should see Uvicorn start on port **8085**.

### Health check

```bash
curl http://127.0.0.1:8085/health
```

```json
{"status":"ok","service":"nexus-agent"}
```

---

## Testing the OpenAI-compatible endpoint with `curl`

### 1. Non-streaming chat completion

```bash
curl -s http://127.0.0.1:8085/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nexus-local",
    "messages": [
      {"role": "system", "content": "You are a concise assistant."},
      {"role": "user", "content": "Explain what NEXUS Agent is in one sentence."}
    ]
  }'
```

Standard OpenAI response:

```json
{
  "id": "chatcmpl-4f1c9a0d2b7e4c8a9b3e5d6f7a8b9c0d",
  "object": "chat.completion",
  "created": 1794630370,
  "model": "simulated_model",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 24, "completion_tokens": 36, "total_tokens": 60}
}
```

### 2. Streaming (SSE)

```bash
curl -N http://127.0.0.1:8085/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nexus-local",
    "stream": true,
    "messages": [{"role": "user", "content": "Count to five."}]
  }'
```

Each event is a `chat.completion.chunk`, terminated by `data: [DONE]` — exactly what the
OpenAI streaming protocol specifies.

### 3. List models

```bash
curl -s http://127.0.0.1:8085/v1/models
```

---

## Using it as an OpenAI backend

### Official `openai` Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8085/v1", api_key="not-needed")

reply = client.chat.completions.create(
    model="nexus-local",
    messages=[{"role": "user", "content": "Hello, NEXUS!"}],
)
print(reply.choices[0].message.content)
```

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="nexus-local",
    base_url="http://127.0.0.1:8085/v1",
    api_key="not-needed",
)
print(llm.invoke("Hello, NEXUS!").content)
```

### OpenHands / any OpenAI-compatible host

Set the base URL to `http://127.0.0.1:8085/v1` and any non-empty API key.
Only `POST /v1/chat/completions` is required for full compatibility.

---

## Optional: use a real local model

NEXUS works without any model. To get real generations, start either backend:

```bash
# Ollama
ollama serve
ollama pull qwen2.5:3b

# or llama.cpp server
llama-server -m ./models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080
```

NEXUS discovers models automatically and picks the largest one that fits within its
**memory-safety budget** (~30% of available RAM by default). If the chosen backend becomes
unreachable at runtime, the request transparently degrades to `SimulatedBackend` instead of
failing.

### Optional API key

```bash
export NEXUS_API_KEY=your-secret
python server.py
```

When set, callers must send `Authorization: Bearer your-secret`.

---

## Memory-safety estimation

`nexus/models/estimator.py` guesses peak RAM for a model before you load it:

* Prefers the real weight file size when the backend reports it.
* Otherwise estimates from parameter count × quantization factor
  (Q4 ≈ 0.65 B/param, FP16 ≈ 2.10 B/param).
* Adds KV-cache/context overhead plus a 20% safety margin.

`ModelSelector` then keeps only candidates whose estimate fits the RAM budget:

```python
from nexus.models.selector import ModelSelector

result = ModelSelector().select()
print(result["ram_budget_mb"])
print([m["name"] for m in result["candidates"]])
```

> Estimates are approximate, not runtime guarantees.

---

## Project layout

```
server.py                  OpenAI-compatible FastAPI app (port 8085)
nexus/core/runtime.py      NexusRuntime: backend resolution, chat + task execution
nexus/core/execution_loop.py   Step-by-step execution with limits
nexus/planner/planner.py   LLM-driven task decomposition
nexus/models/              selector, estimator, ollama & llama.cpp backends
nexus/hardware/            hardware detection and profile optimization
nexus/adapters/            plugin manifests, permissions, lifecycle
tests/                     unittest suite (20 tests)
```

## Running the tests

```bash
python -m unittest discover -s tests -v
```

## Running the task-execution demo

```bash
python nexus/core/runtime.py
```

---

## Notes & limitations

* The simulated backend returns a canned planning-style answer. It exists to guarantee the
  project always boots, **not** to produce useful intelligence.
* `/v1/completions`, embeddings, and tool/function calling are not implemented — only
  `chat.completions` is required for OpenAI-compatible hosts.
* `stream: true` is supported via SSE; token counting in `usage` is a rough character-based
  approximation.

## License

MIT — see [LICENSE](LICENSE).
