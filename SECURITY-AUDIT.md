# nexus-agent 安全审计报告

审计对象：`zyl87788408/nexus-agent`，基线提交 `4bf1b84`（main）
审计时间：2026-11-22
审计方式：源码审读 + 本地实机 PoC 验证（非纯静态推断）

## 结论摘要

| 编号 | 漏洞 | 等级 | 状态 |
|---|---|---|---|
| NX-1 | `python_eval` 沙箱逃逸 → 未授权任意命令执行 | **严重** | 已修复 |
| NX-2 | 本地服务默认无鉴权 + 无 CSRF 防护 | **高危** | 已修复 |
| NX-3 | `run_shell` 命令黑名单可被绕过 | 中危 | 已修复 |
| NX-4 | 插件目录可被他人写入 → 持久化代码执行 | 中危 | 已修复 |
| NX-5 | 记忆库/任务结果无鉴权可读（隐私泄露） | 中危 | 已修复 |
| NX-6 | 依赖未锁定版本 | 低危 | 已修复 |

修复共两轮：第一轮修 NX-1 与 NX-2 的同源校验；第二轮修完 NX-2 剩余部分及 NX-3～NX-6。
全量测试 `279 passed, 2 subtests passed`，并对真实监听端口做了端到端复测。

---

## NX-1｜`python_eval` 沙箱逃逸导致任意命令执行（严重，已修复）

### 位置
`nexus/tools/registry.py` — `_safe_eval()`、`_ALLOWED_NODES`

### 根因
AST 白名单里同时放行了 `Attribute`、`Call`、`Subscript`。这三者组合足以摆脱表达式沙箱：属性访问可以拿到对象的类，进而沿 MRO 走到解释器内部，再从任意函数的 `__globals__` 里取出 `os.system`。`eval` 时虽然清空了 `__builtins__`，但对属性链无能为力。

### PoC（修复前，实测成功）
```python
# 1) 基础逃逸：拿到所有子类
[].__class__.__base__.__subclasses__()
# → 返回全部已加载类，BYPASS-OK

# 2) 升级为任意命令执行（_wrap_close 在所有子类中的下标为 155）
().__class__.__base__.__subclasses__()[155].__init__.__globals__['system']('id')
# → uid=0(root) gid=1000(openhands)
```

### 实际可利用路径（修复前，实测成功）
服务默认不鉴权，因此**一个 HTTP 请求即可执行系统命令**：

```bash
curl -s -X POST http://127.0.0.1:8085/api/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"python_eval","arguments":{"expression":"().__class__.__base__.__subclasses__()[155].__init__.__globals__[\"system\"](\"id\")"}}'
# → {"ok":true,"output":0,...}   # id 已执行
```

危害：以运行 NEXUS 的用户身份读取任意文件、窃取 `cloud.key`、写入启动项、横向移动到内网。若用户以管理员身份运行（Windows 桌面场景很常见），即为完全失陷。

补充：`ToolSpec.dangerous=True` 字段被定义但**从未被读取**，`call()` 只校验 `scope`，而 `compute` 属于默认授权集合，所以该工具默认可用。

### 修复
- 彻底禁止 `Attribute` 节点，从根上断掉属性链；
- `Call` 仅允许固定白名单内建函数（`abs/min/max/sum/round/len/int/float/str/bool/sorted/pow/divmod`），禁止关键字参数与 lambda；
- 拒绝任何以 `__` 开头的名字；
- 表达式长度上限 2000 字符。

正常算术/字符串运算不受影响（`2**10`、`sum([1,2,3])`、`len('hello')` 等仍可用）。

回归测试：`tests/test_tools.py::test_eval_blocks_attribute_walk_to_os`、`test_eval_still_allows_safe_calls`。

---

## NX-2｜本地服务默认无鉴权且无 CSRF 防护（高危，已修复）

### 位置
`server.py` — `_auth_required()`、`_authorized()`、`_guard()`、`main()`；`nexus/config.py` — `server_api_key()`、`ensure_server_api_key()`

### 根因
`require_auth` 默认不存在，`NEXUS_API_KEY` 未设置时 `_authorized()` 恒返回 `True`，**所有** `/api/*` 端点（含 `/api/tool`、`/api/memory`、`/api/plugins`）都可匿名调用。

需要澄清一个常见误解：绑定 `127.0.0.1` **并不能**挡住浏览器的跨站请求。用户访问任意恶意网页时，该网页的 JS 可以直接 `fetch` 到 `http://127.0.0.1:8085/api/tool`（简单请求无需预检），从而以用户浏览器为跳板触发 NX-1。

### PoC（修复前，实测）
```bash
curl -s http://127.0.0.1:8085/api/memory          # 直接读出全部记忆
curl -s -X POST http://127.0.0.1:8085/api/tool -H 'Content-Type: application/json' \
  -d '{"tool":"python_eval","arguments":{"expression":"1+1"}}'
```

### 修复（两层）
1. **默认开启鉴权**：`_auth_required()` 在未显式配置时返回 `True`——语义从"默认敞开"翻转为"默认关闭"。`main()` 首次启动调用 `ensure_server_api_key()` 生成 `secrets.token_urlsafe(32)` 写入 `data/api.key`（`0600`，且已加入 `.gitignore`），不需要用户配置任何环境变量即可开箱即用。
2. **同源校验**：`_guard` 中间件对非 GET/HEAD/OPTIONS 且带非本机 `Origin` 的请求返回 403。
3. 密钥比较改用 `hmac.compare_digest`，避免逐字节时序侧信道。
4. `/panel` 改为从 `localStorage` 取密钥、401 时提示输入一次，**不再把密钥写进页面**。

### 复测（真实监听 8097 端口）
```
POST /api/tool 无密钥          → 401 invalid_api_key
GET  /api/memory 无密钥        → 401 invalid_api_key
POST /api/tool 有效密钥+恶意 Origin → 403 cross_origin_blocked
POST /api/tool 有效密钥+本机 Origin → 200
```

### 兼容性
`server.require_auth: false` 仍可显式关闭（仅建议配合回环绑定），此时若绑定公网地址，`main()` 拒绝启动。

---

## NX-3｜`run_shell` 命令黑名单可绕过（中危，已修复）

### 位置
`nexus/tools/sandbox.py` — `prepare_command()`、`_check_interpreter()`；`nexus/tools/registry.py` — `_run_shell()`

### 根因
黑名单是正则匹配字符串，且随后只对"看起来像路径"的分词做 jail 校验。命令替换与解释器调用完全没被考虑；`subprocess.run(..., shell=True)` 又让反引号、`$(...)`、管道被真正的 shell 解释。

### PoC（修复前，实测）
```
echo `id`                                   → 放行并执行成功，输出 uid=0(root)
python3 -c "import os;print(os.listdir())"  → 放行
ls $(pwd)                                    → 放行
```

### 修复
不再使用 shell。`prepare_command()` 校验后返回 **argv 列表**，`_run_shell` 以 `shell=False` 执行：
- 显式拒绝 `| & ; < > $ \` 反引号、换行、通配符等 shell 元字符（引号内的除外），提示"参数是直接传递的，不支持 shell 语法"，而不是静默改变语义；
- 可执行文件白名单：`echo/cat/ls/dir/pwd/head/tail/wc/grep/find/sort/uniq/cut/tr/sed/awk/python/python3`；
- 解释器额外限制：拒绝 `-c` / `-m`，参数必须是 jail 内脚本文本，杜绝 `python3 -c` 这类"无沙箱解释器"；
- 原有敏感路径（`.ssh`、`id_rsa` 等）与 jail 路径校验继续生效。

### 复测
```
echo `id`     → 拒绝（shell syntax is not supported）
echo $(id)    → 拒绝
ls | wc -l    → 拒绝
echo hi && id → 拒绝
sh -c id      → 拒绝（'sh' 不在白名单）
python3 -c …  → 拒绝（inline code execution not permitted）
echo a b      → 通过，输出 "a b"
```

### 残余风险
白名单内的 `sed -e`/`awk` 仍可读写文件（受 jail 约束）、`python3 script.py` 可执行 jail 内任意脚本——这是该工具的设计用途。真正的强隔离需 seccomp/命名空间（Linux）或 Job Object + 受限令牌（Windows），未在本次实施。

---

## NX-4｜插件目录可被他人写入导致持久化代码执行（中危，已修复）

### 位置
`nexus/adapters/loader.py` — `_check_ownership()`、`load()`；`server.py` — `/api/plugins/*`

### 说明
插件本质就是导入并执行任意 Python 模块（`spec_from_file_location` + `exec_module`），这是插件系统的固有设计。风险在于：启用接口曾无鉴权（NX-2 已修），且 `plugins/` 若可被他人写入，攻击者即可"投放插件 → 启用 → 持久化 RCE"，重启后依然生效。

### 修复
- 启用/禁用接口现由默认鉴权覆盖（NX-2）；
- `load()` 在 `exec_module()` 前校验插件目录：若为 group/other 可写、或 owner 不是当前用户，抛 `PluginLoadError` 拒绝加载；
- 只检查插件自身目录，不向上追溯到 `/tmp` 等本就公共可写的父目录（避免误报）。

---

## NX-5｜记忆库与任务结果无鉴权可读（中危，已修复）

`GET /api/memory`、`GET /api/memory/search`、`GET /api/tasks`、`GET /api/status` 曾可匿名读取，实测直接返回记忆内容。这些数据可能包含工作记录、路径、片段化凭据。

随 NX-2 一并修复：默认鉴权对所有端点生效，`GET /api/memory` 无密钥现返回 401。仅 `/health` 与 `/v1/models` 保持公开（无敏感数据，供探活与客户端发现）。

---

## NX-6｜依赖未锁定版本（低危，已修复）

`requirements.txt` 由 `>=` 下限改为精确锁定为本次实测版本：
```
fastapi==0.162.7
uvicorn==0.54.0
pydantic==2.14.3
pydantic-settings==2.16.3
```
可选加固（未实施）：提供带 hash 的 `requirements.lock` 并 `pip install --require-hashes`；离线包内置已验证 wheel。

---

## 已确认安全的点（正向结论）

审计中重点验证了以下位置，未发现问题，供参考：

- **路径穿越**：`Sandbox.resolve()` 先 `resolve(strict=False)` 展开符号链接，再 `relative_to(root)` 校验，并拦截 `.ssh`/`.aws`/`id_rsa` 等敏感名。设计正确。
- **SQL 注入**：`nexus/memory/store.py` 全部使用参数化查询（含 FTS 的 `MATCH ?`），仅 `kind`/`limit` 拼接的是固定片段，无可注入点。
- **密钥泄露**：`.gitignore` 覆盖 `.env`、`data/*`、`*.db`、`*.jsonl`；`git log --all -p` 全历史扫描未发现 `sk-`/`ghp_`/`AKIA`/私钥等硬编码凭据。仓库仅跟踪 74 个文件，`data/` 只跟踪 `.gitkeep`。`save_cloud_key()` 写入 `chmod 0600`，且 `GET /api/cloud` 只返回 `has_key` 布尔值，从不回显密钥。
- **出网控制**：`nexus/net.py` 的 `NetworkPolicy` 默认 `OFFLINE`，仅 loopback 放行，云端需 `privacy.allow_cloud` 显式同意。`is_loopback()` 对主机名做全地址校验，DNS 失败时 fail-closed。`file://` 等非 http(s) scheme 直接拒绝。对云元数据地址 `169.254.169.254` 判定为非 loopback。
- **XSS**：`/panel` 页面 5 处 `innerHTML` 写入的均为布尔值（`cloud_allowed`、`cloud_enabled` 等），无用户可控内容注入点。
- **工具调用不回灌**：`/v1/chat/completions` 仅把 `tool_calls` 返回给调用方，不会自动执行，符合 OpenAI 协议语义，避免了"模型输出即执行"。
- **错误处理**：`chat_completions` 捕获异常后只回 `str(exc)`，不泄露堆栈。

---

## 修复提交

第一轮（NX-1、NX-2 同源校验）：
```
fix(security): close the python_eval escape and cross-site tool calls
  nexus/tools/registry.py | nexus/tools/... server.py | tests/test_tools.py
```

第二轮（NX-2 默认鉴权、NX-3 shell、NX-4 插件、NX-5 读接口、NX-6 依赖）：
```
fix(security): require auth by default and remove the shell from run_shell
  .gitignore | README.md | nexus/adapters/loader.py | nexus/config.py
  nexus/tools/registry.py | nexus/tools/sandbox.py | requirements.txt
  server.py | tests/test_api.py | tests/test_tools.py | tests/conftest.py
```

测试：`279 passed, 2 subtests passed`。

## 优先处置建议

1. **升级到最新 main**：NX-1 原可直接拿 shell 且无需任何前置条件。
2. **更新本地客户端**：鉴权现默认开启，客户端需带 `Authorization: Bearer $(cat data/api.key)`。
3. **可选加固（未实施）**：`requirements.lock` + hash 校验；Linux 上用 seccomp/命名空间、Windows 上用 Job Object 做进程级强隔离。
