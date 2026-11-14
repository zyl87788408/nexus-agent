# NEXUS Agent

> **A local model-plane proxy: it takes over an agent engine's model API calls and serves them from your own machine.**

NEXUS does **not** replace OpenHands, LangChain or any other agent engine. The engine stays
in charge of planning, tool execution and its agent loop. NEXUS is plugged in at the
*model API layer* — the engine is pointed at NEXUS's base URL instead of a cloud model, and
NEXUS serves those `POST /v1/chat/completions` calls from a local model.

```
host engine  --(OpenAI API)-->  NEXUS  --(Ollama / llama.cpp)-->  local model
```

Because the host drives its loop with tool calls, NEXUS forwards `tools` verbatim and
returns OpenAI-shaped `tool_calls` (`id`, `type: "function"`, `arguments` as a JSON
string), so the host's tool loop keeps working unchanged. Verified with the official
`openai` SDK end to end: request → `tool_calls` → host executes → result sent back →
final answer.

Two design rules drive the whole codebase:

1. **It never reports work it did not do.** A task only reaches `completed` after every
   step produced verified output. Every response states whether it came from a real model
   (`simulated: false`) or the fallback (`simulated: true`).
2. **OFFLINE mode cannot reach the network.** All model and API egress passes through
   `nexus/net.py`, which refuses non-loopback connections before a socket is opened.
   Shell execution is the one path that could bypass that gate, so enabling
   `tools.allow_shell` together with OFFLINE mode is rejected at startup rather than
   running with a guarantee the engine cannot keep. In `auto`/`online` mode shell
   commands are ordinary processes and are not subject to the egress policy.

---

## What it does

| Capability | Details |
| --- | --- |
| **OpenAI-compatible API** | `POST /v1/chat/completions` with standard `chat.completion` JSON, SSE streaming, and `GET /v1/models`. |
| **Three operating modes** | `offline` (loopback only, structurally), `online` (cloud allowed with consent), `auto` (local first, cloud as fallback). |
| **Verified task execution** | Plan, execute, verify. Evidence is mandatory; retries are bounded; per-step journal and resumable checkpoints. |
| **Local model management** | Discovers Ollama / llama.cpp models, probes whether they actually run, and picks the largest that fits the RAM budget. |
| **Cloud model integration** | Any OpenAI-compatible endpoint, gated by mode and explicit consent. |
| **Hardware adaptation** | Detects CPU / RAM / GPU / VRAM / disk and derives a model budget and execution profile from what is really available. |
| **Plugin system** | Manifest validation, real module import under a per-plugin module name, permission scopes, lifecycle hooks, fault isolation. |
| **Long-term memory** | SQLite with FTS5 full-text search (LIKE fallback), tags, importance, backup/restore. |
| **Multi-agent collaboration** | Specialist agents run with bounded concurrency, isolated failures, and conflict detection. |
| **Tool calling + sandbox** | Filesystem jail, traversal/symlink escape rejection, credential-path denial, dangerous-command deny-list, disabled by default. |
| **Runtime status** | Mode, hardware profile, tools, plugins, memory counts, and whether output is simulated. |

---

## Quick start

```bash
git clone https://github.com/zyl87788408/nexus-agent
cd nexus-agent

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python server.py                   # http://127.0.0.1:8085
```

```bash
curl http://127.0.0.1:8085/health
# {"status":"ok","service":"nexus-agent","version":"2.0.0"}
```

### With a real local model

```bash
ollama serve &
ollama pull qwen2.5:0.5b           # or any size your RAM allows

curl -s http://127.0.0.1:8085/api/status | python -m json.tool
```

NEXUS detects the model, verifies it responds, and uses it. No configuration needed.

---

## Operating modes

```bash
# inspect
curl http://127.0.0.1:8085/api/modes

# switch (optionally persisted to the config file)
curl -X POST http://127.0.0.1:8085/api/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"offline","persist":true}'
```

| Mode | Local models | Cloud models | Behaviour |
| --- | --- | --- | --- |
| `offline` | yes | blocked before any socket opens | Fully air-gapped. |
| `online` | yes | with `privacy.allow_cloud` | Explicit opt-in required. |
| `auto` | preferred | only as fallback, with consent | Local-first. |

The mode switch response reports `verified`: whether the new mode was confirmed working
(for example, offline is verified only when a local model actually answers). It reports
failure honestly rather than claiming success.

---

## Honest failure

When no model is available, the engine does not pretend the work succeeded:

```json
{
  "status": "failed",
  "simulated": true,
  "error": "no model backend available; refusing to fake execution"
}
```

Chat still degrades to a clearly-labelled simulated answer so the API stays usable, and
every response carries `nexus.simulated`:

```json
{"model": "simulated_model", "nexus": {"source": "simulated", "simulated": true}}
```

---

## HTTP API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Liveness probe. |
| `/api/status` | GET | Mode, hardware, tools, plugins, memory. |
| `/api/modes` | GET | Available and current mode. |
| `/api/mode` | POST | Switch mode, returns verification result. |
| `/api/task` | POST | Run a goal through the verified execution loop. |
| `/api/team` | POST | Run specialist agents on a goal. |
| `/api/tools` | GET | List tools and scopes. |
| `/api/tool` | POST | Call a tool inside the sandbox. |
| `/api/memory` | GET/POST | List or add memories. |
| `/api/memory/search` | GET | Full-text search. |
| `/api/memory/{id}` | DELETE | Delete a memory. |
| `/api/plugins` | GET | Plugin status. |
| `/v1/models` | GET | OpenAI-compatible model list. |
| `/v1/chat/completions` | POST | OpenAI-compatible chat, with `stream: true`. |

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8085/v1", api_key="not-needed")
print(client.chat.completions.create(
    model="nexus-local",
    messages=[{"role": "user", "content": "Hello, NEXUS!"}],
).choices[0].message.content)
```

### Streaming

```bash
curl -N http://127.0.0.1:8085/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nexus-local","stream":true,"messages":[{"role":"user","content":"Count to five."}]}'
```

Each event is a `chat.completion.chunk`, terminated by `data: [DONE]`.

---

## Security defaults

* Binds to `127.0.0.1`. The server refuses to start on a public host without an API key.
* API key (`NEXUS_API_KEY`) required on all mutating endpoints when configured.
* CORS is an allow-list, never `*`.
* Config file written `0600`; secrets are stored as environment-variable names, never values.
* Tools run inside a filesystem jail; shell execution is off unless explicitly granted,
  and cannot be granted in OFFLINE mode.

```bash
export NEXUS_API_KEY=your-secret
python server.py
```

---

## Plugins

A plugin is a directory with a `manifest.json` and a Python module. Every plugin found
under the plugin directory is activated at startup, in name order: the host imports the
module and calls `setup(context)`, and whatever the plugin registers there is live in the
running engine. The directory is `plugins/` by default and can be moved with the
`plugins` config key.

Plugins are trusted code and run in-process: the manifest's `permissions` gate which
host services (`tools`, `model`, `memory`, `filesystem`) the plugin can reach through
`context.services`, not what Python code it may execute. Only install plugins you trust.

```
plugins/
  hello_tool/
    manifest.json
    plugin.py
```

```json
{
  "name": "hello_tool",
  "version": "1.0.0",
  "host": ">=2.0",
  "permissions": ["tools"],
  "entry": "plugin.py"
}
```

`name`, `version` and `host` are required. `permissions` must be a subset of
`filesystem`, `model`, `tools`, `memory`; a manifest that fails validation is rejected
rather than loaded in a degraded state.

`setup(context)` receives the host services, so a plugin can add tools to the registry
the engine actually uses:

```python
from nexus.tools.registry import ToolSpec

_registry = None

def setup(context):
    global _registry
    _registry = context.services["tools"]
    _registry.register(ToolSpec(
        name="hello_tool",
        description="Return a greeting for the given name.",
        handler=lambda name="world": f"hello, {name}",
        scope="read",
    ))

def shutdown():
    if _registry is not None:
        _registry.unregister("hello_tool")
```

The full working version is in `examples/plugins/hello_tool/` and is exercised by the
test suite. Copy it into `plugins/` (or set `"plugins": "<path>"`) and restart; no code
change is needed. `GET /api/plugins` reports what was discovered and activated:

```json
{"discovered": ["hello_tool"], "installed": ["hello_tool"],
 "active": ["hello_tool"], "enabled": {"hello_tool": true}, "errors": {}}
```

### Enabling and disabling at runtime

```
POST /api/plugins/{name}/disable    # turn it off, and keep it off
POST /api/plugins/{name}/enable     # turn it back on, immediately
POST /api/plugins/{name}            # {"enabled": false} - same thing, one route
```

Disabling is not cosmetic: the plugin's tools are removed from the registry and its
permissions are revoked, so calls fail with `unknown tool` rather than merely being
hidden from a list. Enabling imports and runs `setup()` again, so the tools come back.
The choice is written to `plugins_disabled` in the config file and survives a restart —
otherwise a restart would quietly undo an operator's decision to turn something off.

Both routes require the API key when one is configured, and return 404 for a plugin that
does not exist. There is still no endpoint to *install* a plugin from a URL or archive:
plugins are directories on disk, picked up at startup.

A plugin may only register tools with names it owns. Registering a name that a host tool
already uses (including with `replace=True`) fails with `PermissionError`, and so does
unregistering a tool the plugin did not register. Without that, a plugin holding only the
`tools` permission could replace `run_shell` and rewrite its scope.

A plugin that raises during import or `setup()` is contained: the error is recorded in
`/api/plugins` under `errors`, that plugin is not activated, and the rest of the engine —
including other plugins — keeps running. Anything it registered before failing is rolled
back, so a half-initialised plugin never leaves callable tools in the registry.

A manifest that fails validation, and a second plugin declaring a name that is already
taken, are both reported under `errors` keyed by directory rather than dropped silently.

Service access is gated fail-closed. `context.services` is a read-only mapping that
checks a declared permission on every read; reading a name that is not a host capability
raises `PermissionError` rather than reporting "missing". The host also refuses to start
if it is handed a service with no permission mapping, because such a service would be
readable by every plugin regardless of its manifest.

---

## Hardware adaptation

`nexus/hardware/optimizer.py` derives a model budget from memory that is actually
available rather than a flat percentage, which on a 4 GB machine previously yielded a
budget smaller than any real model.

| Box | Profile | Agents | Context |
| --- | --- | --- | --- |
| 2 GB | Lite | 1 | small |
| 4 GB | Lite | 1 | 2048 |
| 8 GB | Balanced | 2 | medium |
| 16 GB+ | Performance | 4+ | large |

GPU/VRAM is used when genuinely present; a machine without a GPU reports `gpu: null`
rather than inventing capability.

---

## Project layout

```
server.py                       FastAPI app, OpenAI-compatible surface (port 8085)
nexus/config.py                 Persistent config, mode enum
nexus/net.py                    Single network egress point; offline enforcement
nexus/hardware/                 Detection and profile optimisation
nexus/models/                   Discovery, probing, selection, cloud backend
nexus/core/runtime.py           Wiring of every subsystem; chat + tasks
nexus/core/execution_loop.py    Plan, execute, verify, retry, resume
nexus/core/journal.py           Append-only journal and checkpoints
nexus/planner/planner.py        Goal decomposition with tolerant JSON parsing
nexus/tools/                    Sandbox, tool registry, default tools
nexus/memory/store.py           SQLite long-term memory
nexus/agents/, nexus/orchestration/   Specialist agents and coordination
nexus/adapters/                 Plugin loader, manager, permissions, context
tests/                          198 tests
```

---

## Tests

```bash
python -m unittest discover -s tests -v
```

198 tests covering modes, the offline guarantee, execution verification, retries and
resume, sandbox escapes, memory search, multi-agent concurrency, plugin isolation, the
HTTP surface and security.

The offline guarantee is tested with a socket-level spy: in `offline` mode the suite asserts
that zero non-loopback connections are attempted.

They also pass with the network namespace fully removed:

```bash
unshare -n python -m unittest discover -s tests
```

---

## Limitations

* Only `chat.completions` is implemented (no embeddings or `/v1/completions`).
* `usage` token counts are character-based approximations.
* Quality depends entirely on the model you point it at; the fallback backend produces
  canned text and is always labelled as simulated.
* Memory-safety budgets are estimates, not runtime guarantees.

## License

MIT, see [LICENSE](LICENSE).
