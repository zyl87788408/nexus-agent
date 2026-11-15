# NEXUS — local model-plane plugin

A plugin that sits on a host engine's model API layer and serves those calls from a
local model. The host engine keeps its own planning, tool execution and agent loop;
NEXUS only answers `POST /v1/chat/completions`.

## Core
- Local models over an OpenAI-compatible API
- Automatic hardware adaptation
- Multi-agent collaboration (internal coordinator, bounded concurrency)
- Verified execution with mandatory evidence and resumable checkpoints
- Local memory (SQLite + FTS5)
- Sandboxed tools and per-plugin permission scopes
- Plugin system: manifest validation, lifecycle hooks, fault isolation, runtime enable/disable
- Linux, Windows, macOS
- No cloud dependency in `offline` mode
