"""OpenAI-compatible HTTP server for NEXUS.

Security defaults:
  * binds to 127.0.0.1 (never 0.0.0.0) unless explicitly overridden;
  * requires an API key whenever one is configured, and *refuses to start*
    on a non-loopback host without one;
  * CORS is an allow-list, not a wildcard.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexus.config import Config, Mode
from nexus.core.runtime import NexusRuntime
from nexus.adapters.loader import PluginLoadError
from nexus.models.registry import discover_models
from nexus.net import CloudBlockedError

config = Config.load()
runtime = NexusRuntime(config)

API_KEY = config.server_api_key()

app = FastAPI(
    title="NEXUS — local model-plane plugin",
    description="Access-layer plugin serving an agent engine's model API calls from a local model.",
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
    # Tool calling is the contract a host engine actually uses: it sends its
    # tool schemas, expects `tool_calls` back, runs them, and asks again.
    # Dropping these fields silently turns every agent turn into plain chat.
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None


class ModeRequest(BaseModel):
    mode: str
    allow_cloud: bool | None = None
    persist: bool = True


class CloudConfigRequest(BaseModel):
    """配置云端 API。key 不落 config.json，单独存 data/cloud.key。"""

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


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
    """All endpoints are authenticated unless the operator opts out.

    Defaulting to "open" meant a single POST from any page in the user's
    browser could drive the tools. Auth is now on unless
    ``server.require_auth`` is explicitly set to false.
    """
    configured = config.get("server.require_auth")
    if configured is None:
        return True
    return bool(configured) or bool(API_KEY)


def _current_api_key() -> str | None:
    """The key to compare against: env/file, refreshed if it appeared later."""
    return API_KEY or config.server_api_key()


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _same_origin(request: Request) -> bool:
    """Reject cross-site browser requests aimed at the local service.

    With no API key the service is open to anything that can reach the port.
    Binding to 127.0.0.1 stops other machines but not the user's own browser:
    any page they open can POST to /api/tool and run commands. Browsers send
    Origin on cross-site POST, so refuse anything whose Origin is not one of
    our own addresses. Requests without Origin (curl, scripts, the desktop
    panel) are unaffected.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urllib.parse.urlparse(origin)
    return (parsed.hostname or "").lower() in _LOCAL_HOSTS


async def _guard(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
        return JSONResponse(
            status_code=403,
            content={"error": {"message": "cross-origin request refused",
                               "type": "permission_error",
                               "code": "cross_origin_blocked"}},
        )
    return await call_next(request)


app.middleware("http")(_guard)


def _authorized(authorization: str | None) -> bool:
    if not _auth_required():
        return True
    if not authorization:
        return False
    token = authorization.replace("Bearer ", "", 1).strip()
    key = _current_api_key()
    # constant-time compare: a plain == leaks the key one byte at a time
    return bool(key) and hmac.compare_digest(token, key)


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": "nexus-agent", "version": "2.0.0"}


@app.get("/api/status")
def status():
    return runtime.status()


@app.get("/api/cloud")
def get_cloud():
    """返回云端配置（绝不返回 key 本身，只返回是否已配置）。"""
    return {
        "enabled": config.cloud_enabled,
        "base_url": config.get("cloud.base_url"),
        "model": config.get("cloud.model"),
        "has_key": bool(config.cloud_api_key()),
    }


@app.post("/api/cloud")
def set_cloud(request: CloudConfigRequest,
              authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")

    if request.base_url is not None:
        url = request.base_url.strip()
        if url and not url.startswith(("http://", "https://")):
            return _error(400, "base_url must start with http:// or https://")
        config.set("cloud.base_url", url or "https://api.openai.com/v1")
    if request.model is not None:
        config.set("cloud.model", request.model.strip())
    if request.enabled is not None:
        config.set("cloud.enabled", bool(request.enabled))
    if request.api_key is not None:
        if request.api_key.strip():
            config.save_cloud_key(request.api_key)
        else:
            # 空字符串 = 清除已保存的密钥。以 config.json 所在目录为准，
            # 不用 cwd —— 服务可能从任意目录启动。
            try:
                (config.path.parent / "cloud.key").unlink()
            except OSError:
                pass

    config.save()
    return {"ok": True, **get_cloud()}


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


def _normalise_tool_calls(raw) -> list[dict]:
    """Map backend tool_calls onto the OpenAI wire format.

    Ollama returns `{"function": {"name", "arguments": {...}}}` with no `id`;
    OpenAI sends `{"id", "type": "function", "function": {"name",
    "arguments": "<json string>"}}`. Callers key their tool results off `id`
    and parse `arguments` as a JSON string, so both gaps must be closed.
    """
    if not raw:
        return []
    normalised = []
    for index, call in enumerate(raw):
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        normalised.append({
            "id": call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": fn.get("name") or "",
                         "arguments": args},
        })
    return normalised


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
    """List the models actually available locally.

    Clients populate their model picker from this endpoint, so returning a
    single synthetic name made every installed model invisible and left users
    unable to choose. Names are reported as installed; affordability is decided
    per request, so listing a large model here does not force it to load.
    """
    try:
        discovered = discover_models()
    except Exception:
        discovered = []

    data = [
        {
            "id": item.get("name", ""),
            "object": "model",
            "created": int(time.time()),
            "owned_by": item.get("backend") or "local",
        }
        for item in discovered
        if item.get("name")
    ]
    if not data:
        # Keep the endpoint usable with no model installed: an empty list makes
        # some clients fail to start up rather than showing "no model".
        data = [{"id": "nexus-local", "object": "model",
                 "created": int(time.time()), "owned_by": "nexus"}]
    return {"object": "list", "data": data}


# ---------------------------------------------------------------- control panel
_PANEL_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXUS 控制面板</title>
<style>
:root{--bg:#12141a;--card:#1b1e27;--line:#2b3040;--txt:#e6e8ee;--dim:#8b93a7;
      --local:#2ecc71;--api:#4a9eff;--auto:#b46cff;--warn:#f5a524;--err:#e5484d}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI",
     "Microsoft YaHei",sans-serif;padding:16px;min-height:100vh}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px}
h1{font-size:15px;font-weight:600;margin-bottom:2px;letter-spacing:.3px}
.sub{color:var(--dim);font-size:12px;margin-bottom:14px}
.opts{display:flex;flex-direction:column;gap:8px}
.opt{display:flex;align-items:center;gap:10px;padding:11px 13px;border-radius:8px;
     border:1px solid var(--line);background:#161922;cursor:pointer;transition:.15s;
     user-select:none;position:relative}
.opt:hover{border-color:#3d4459;background:#1a1e29}
.opt.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,#161922)}
.dot{width:9px;height:9px;border-radius:50%;background:#394054;flex:0 0 auto;transition:.15s}
.opt.on .dot{background:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
.opt .t{font-weight:500}
.opt .d{color:var(--dim);font-size:11.5px;line-height:1.4}
.st{margin-left:auto;font-size:11px;padding:2px 7px;border-radius:5px;background:#232838;color:var(--dim)}
.opt.on .st{background:color-mix(in srgb,var(--accent) 20%,transparent);color:var(--accent)}
.row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;
     border-bottom:1px solid #22263220}
.row:last-child{border-bottom:none}
.row .k{color:var(--dim);font-size:12.5px}
.row .v{font-size:12.5px;font-weight:500;max-width:60%;text-align:right;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ok{color:var(--local)}.bad{color:var(--err)}.warn{color:var(--warn)}
#msg,#msg2{padding:9px 12px;border-radius:7px;font-size:12.5px;display:none;margin-top:4px}
#msg.show,#msg2.show{display:block}
#msg.ok,#msg2.ok{background:#1a2e22;color:#7ee2a8;border:1px solid #24523a}
#msg.err,#msg2.err{background:#2e1a1c;color:#f08c90;border:1px solid #52242a}
#msg.info,#msg2.info{background:#1a2233;color:#8fb8f0;border:1px solid #243a52}
.foot{color:var(--dim);font-size:11px;text-align:center;margin-top:6px;line-height:1.7}
a{color:var(--api);text-decoration:none}
.grp{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.6px;
     margin:0 0 7px 2px}
.fl{display:block;color:var(--dim);font-size:11.5px;margin:9px 0 0}
.fl input{width:100%;margin-top:4px;padding:7px 9px;border-radius:6px;
  border:1px solid var(--line);background:#161922;color:var(--txt);
  font:12.5px/1.4 inherit;outline:none}
.fl input:focus{border-color:var(--api)}
.btn{width:100%;margin-top:11px;padding:9px;border-radius:6px;border:1px solid var(--line);
  background:#20283a;color:var(--txt);font:13px inherit;cursor:pointer;transition:.15s}
.btn:hover{background:#27324a;border-color:var(--api)}
</style></head><body>

<div class="card">
  <h1>NEXUS 模型来源</h1>
  <div class="sub">点一下即生效，客户端无需改动</div>
  <div class="opts">
    <div class="opt" data-mode="offline" style="--accent:var(--local)">
      <span class="dot"></span>
      <span><span class="t">本地模型</span><br>
            <span class="d">只用自己的电脑，绝不联网</span></span>
      <span class="st">离线</span>
    </div>
    <div class="opt" data-mode="auto" style="--accent:var(--auto)">
      <span class="dot"></span>
      <span><span class="t">自动</span><br>
            <span class="d">优先本地；本地不可用时自动切 API</span></span>
      <span class="st">推荐</span>
    </div>
    <div class="opt" data-mode="online" style="--accent:var(--api)">
      <span class="dot"></span>
      <span><span class="t">云端 API</span><br>
            <span class="d">始终用 API，效果最好但需联网</span></span>
      <span class="st">联网</span>
    </div>
  </div>
  <div id="msg"></div>
</div>

<div class="card">
  <div class="grp">当前状态</div>
  <div class="row"><span class="k">模式</span><span class="v" id="s-mode">—</span></div>
  <div class="row"><span class="k">正在使用</span><span class="v" id="s-model">—</span></div>
  <div class="row"><span class="k">来源</span><span class="v" id="s-src">—</span></div>
  <div class="row"><span class="k">本地模型</span><span class="v" id="s-local">—</span></div>
  <div class="row"><span class="k">API 授权</span><span class="v" id="s-allow">—</span></div>
</div>

<div class="card">
  <div class="grp">API 设置</div>
  <div class="row"><span class="k">状态</span><span class="v" id="s-cloud">—</span></div>
  <div class="row"><span class="k">密钥</span><span class="v" id="s-key">—</span></div>
  <label class="fl">服务地址
    <input id="i-url" placeholder="https://api.openai.com/v1" spellcheck="false">
  </label>
  <label class="fl">模型名
    <input id="i-model" placeholder="gpt-4o-mini" spellcheck="false">
  </label>
  <label class="fl">API 密钥
    <input id="i-key" type="password" placeholder="sk-…（留空则不改）" spellcheck="false">
  </label>
  <button id="save" class="btn">保存并启用</button>
  <div id="msg2"></div>
</div>

<div class="foot">
  <span id="link"><a href="/docs" target="_blank">接口文档</a> ·
  <a href="/health" target="_blank">健康检查</a></span><br>
  <span id="ts"></span>
</div>

<script>
const $=s=>document.querySelector(s);
const SRCN={local:"本地",cloud:"云端 API",simulated:"兜底(未就绪)"};

// The service requires an API key by default. Read it from localStorage so the
// panel keeps working, and ask once on first use. It is never rendered into
// this page - the page itself is served without auth.
const KEY_STORE="nexus_api_key";
function apiKey(){ return localStorage.getItem(KEY_STORE) || ""; }
function authHeaders(extra){
  const h = Object.assign({}, extra||{});
  const k = apiKey();
  if(k) h["Authorization"]="Bearer "+k;
  return h;
}
async function api(path, opts){
  opts = opts || {};
  opts.headers = authHeaders(opts.headers);
  const r = await fetch(path, opts);
  if(r.status===401){
    const k = prompt("该服务需要 API Key\\n（见 data/api.key）");
    if(k){ localStorage.setItem(KEY_STORE, k.trim()); return api(path, opts); }
  }
  return r;
}

function flash(el,txt,kind){
  el.textContent=txt; el.className="show "+kind;
  clearTimeout(el._t); el._t=setTimeout(()=>el.className="",4200);
}
function paint(mode,allowed){
  document.querySelectorAll(".opt").forEach(o=>{
    o.classList.toggle("on", o.dataset.mode===mode);
  });
}
async function refresh(){
  try{
    const m=await (await api("/api/modes")).json();
    paint(m.current,m.cloud_allowed);
    $("#s-mode").textContent={offline:"本地模型",auto:"自动",online:"云端 API"}[m.current]||m.current;
    $("#s-allow").innerHTML = m.cloud_allowed
      ? '<span class="ok">已授权</span>' : '<span class="warn">未授权</span>';
    $("#s-cloud").innerHTML = m.cloud_enabled
      ? '<span class="ok">已启用</span>' : '<span class="bad">未启用</span>';

    const s=await (await api("/api/status")).json();
    const src=s.active_source||"—";
    $("#s-src").textContent=SRCN[src]||src;
    $("#s-src").className="v "+(src==="local"?"ok":src==="cloud"?"":"warn");
    $("#s-model").textContent=s.active_model||"未选择";
    $("#s-local").innerHTML = s.simulated
      ? '<span class="warn">未检测到可用模型</span>'
      : '<span class="ok">就绪</span>';

    const c=await (await api("/api/cloud")).json();
    $("#s-key").innerHTML = c.has_key
      ? '<span class="ok">已保存</span>' : '<span class="bad">未设置</span>';
    if(document.activeElement!==$("#i-url"))   $("#i-url").value=c.base_url||"";
    if(document.activeElement!==$("#i-model")) $("#i-model").value=c.model||"";
    $("#ts").textContent="更新于 "+new Date().toLocaleTimeString();
  }catch(e){
    $("#s-mode").innerHTML='<span class="bad">无法连接服务</span>';
  }
}
async function setMode(mode){
  const need = mode==="online" || mode==="auto";
  const body={mode:mode, persist:true};
  if(need) body.allow_cloud=true;
  if(mode==="offline") body.allow_cloud=false;

  try{
    const r=await api("/api/mode",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){ flash($("#msg"),"切换失败："+(d.detail||d.message||r.status),"err"); return; }
    if(d.verified){
      flash($("#msg"),"已切换到「"+({offline:"本地模型",auto:"自动",online:"云端 API"}[mode])+"」","ok");
    }else{
      const e=(d.detail&&d.detail.error)||"后端暂不可用";
      flash($("#msg"),"已切换，但当前不可用："+e,"err");
    }
    await refresh();
  }catch(e){
    flash($("#msg"),"网络错误："+e.message,"err");
  }
}
document.querySelectorAll(".opt").forEach(o=>{
  o.onclick=()=>setMode(o.dataset.mode);
});

$("#save").onclick=async()=>{
  const body={enabled:true};
  const url=$("#i-url").value.trim();   if(url)   body.base_url=url;
  const mdl=$("#i-model").value.trim(); if(mdl)   body.model=mdl;
  const key=$("#i-key").value.trim();   if(key)   body.api_key=key;
  try{
    const r=await api("/api/cloud",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){ flash($("#msg2"),"保存失败："+(d.detail||r.status),"err"); return; }
    $("#i-key").value="";
    flash($("#msg2"),"已保存。点上方「API」或「自动」即可使用","ok");
    await refresh();
  }catch(e){ flash($("#msg2"),"网络错误："+e.message,"err"); }
};

refresh(); setInterval(refresh,5000);
</script></body></html>"""


@app.get("/panel", response_class=HTMLResponse)
def panel():
    """模型来源控制面板 — 浏览器可开，也可做成桌面悬浮窗。"""
    return HTMLResponse(_PANEL_HTML)


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest,
                     authorization: str | None = Header(default=None)):
    if not _authorized(authorization):
        return _error(401, "Invalid API key", "authentication_error", "invalid_api_key")
    if not request.messages:
        return _error(400, "'messages' must not be empty")

    messages = [m.model_dump(exclude_none=True) for m in request.messages]

    try:
        message, model_used, source = runtime.chat_message(
            messages, tools=request.tools, model=request.model)
        text = message.get("content") or ""
        tool_calls = _normalise_tool_calls(message.get("tool_calls"))
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
    assistant: dict = {"role": "assistant", "content": text}
    if tool_calls:
        assistant["tool_calls"] = tool_calls
        assistant["content"] = text or None
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_used,
        "choices": [
            {
                "index": 0,
                "message": assistant,
                "finish_reason": "tool_calls" if tool_calls else "stop",
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


def _setup_file_logging() -> Path:
    """把运行日志落到 data/logs/nexus.log。

    开机自启时进程无可见窗口，日志是远程排障的唯一线索。
    """
    import logging

    log_dir = Path(sys.argv[0]).resolve().parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "nexus.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))

    # 只挂在 root 上：uvicorn 的 logger 会向上冒泡，
    # 若同时给 "uvicorn"/"uvicorn.error" 挂 handler，每条会写 2-3 遍。
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # 简单滚动：超过 5 MB 就轮换一次，避免长期运行撑满磁盘
    try:
        if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
            backup = log_dir / "nexus.log.1"
            if backup.exists():
                backup.unlink()
            log_file.rename(backup)
    except OSError:
        pass

    return log_file


def main() -> int:
    import uvicorn

    host = config.get("server.host", "127.0.0.1")
    port = int(config.get("server.port", 8085))

    global API_KEY
    auth_on = _auth_required()
    if auth_on and not _current_api_key():
        try:
            API_KEY = config.ensure_server_api_key()
        except OSError as exc:
            print(f"ERROR: cannot create the API key file: {exc}", file=sys.stderr)
            return 2

    # Defence in depth: a public interface must never be unauthenticated.
    if host not in {"127.0.0.1", "localhost", "::1"} and not auth_on:
        print(
            f"ERROR: refusing to bind {host} without authentication.\n"
            "Leave server.require_auth on, or bind 127.0.0.1 for local-only use.",
            file=sys.stderr,
        )
        return 2

    log_file = _setup_file_logging()
    print(f"NEXUS listening on http://{host}:{port} (mode={config.mode.value}, "
          f"auth={'on' if auth_on else 'off'})")
    if auth_on:
        print(f"API key: {(config.path.parent / 'api.key')}")
    print(f"log file: {log_file}")
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
