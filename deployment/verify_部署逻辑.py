"""离线验证 ② 一键部署.bat 的模型安装决策流（不依赖 Windows）。

把批处理子程序按 cmd 语义解释执行，覆盖 10 个分支组合。
用法: python3 verify_部署逻辑.py
"""

import re
import sys
from pathlib import Path

BAT = Path(__file__).with_name("② 一键部署.bat")


def _load():
    body = BAT.read_bytes().decode("gbk").split("\r\n:MODELSETUP\r\n", 1)[1]
    body = body.split("\r\n:SCRIPT_END")[0]
    lines = [l.strip() for l in body.split("\r\n")]
    pos = {l[1:]: i for i, l in enumerate(lines) if l.startswith(":")}
    return lines, pos


L, POS = _load()


def run(has_model, ollama_up, llama_up, cli, mem, ans, pull_ok=True):
    st = dict(has_model=has_model, ollama_up=ollama_up,
              llama_up=llama_up, cli=cli, pull_ok=pull_ok)
    env = {"HASMODEL": "0", "ANS": ans, "MEMGB": "", "REC": "", "RECSIZE": "",
           "LOCALAPPDATA": r"C:\Users\u\AppData\Local", "PATH": "sys"}
    ef, pulls, seen = 0, [], []
    i = 0
    for _ in range(400):
        if i >= len(L):
            break
        s = L[i]; i += 1
        if not s or s.lower().startswith(("rem", "::")):
            continue
        if s.startswith(":"):
            seen.append(s[1:]); continue
        s = re.sub(r'!(\w+)!', lambda m: env.get(m.group(1), ""), s)
        lo = s.lower()

        if "ollama serve" in lo:
            st["ollama_up"] = True; continue
        if lo.startswith(("echo", "timeout", "start ", "del ")):
            continue
        if "where ollama" in lo:
            ef = 0 if st["cli"] else 1; continue
        if "11434/api/tags" in lo:
            ef = 0 if st["ollama_up"] else 1; continue
        if "8080/v1/models" in lo:
            ef = 0 if st["llama_up"] else 1; continue
        if lo.startswith("ollama pull"):
            pulls.append(s.split("ollama pull", 1)[1].strip())
            ef = 0 if st["pull_ok"] else 1; continue
        if "findstr" in lo:
            ef = 0 if st["has_model"] else 1; continue
        if "powershell" in lo:
            env["MEMGB"] = str(mem); continue
        if re.match(r'^set /p', lo):
            mv = re.match(r'set /p "(\w+)=', s, re.I)
            if mv:
                env[mv.group(1)] = ans
            continue
        if lo.startswith('set "'):
            kv = s[5:].rstrip('"')
            if "=" in kv:
                k, v = kv.split("=", 1); env[k] = v
            continue

        mb = re.match(r'if (?:(not) )?errorlevel 1 \($', s, re.I)
        if mb:
            cond = (ef == 0) == bool(mb.group(1))
            depth, j = 1, i
            while j < len(L) and depth > 0:
                depth += L[j].count("(") - L[j].count(")")
                j += 1
            if not cond:
                i = j
            continue

        m = re.match(r'if (\d+) geq (\d+) set "(\w+)=(.+)"$', s, re.I)
        if m:
            if int(m.group(1)) >= int(m.group(2)):
                env[m.group(3)] = m.group(4).strip('"')
            continue
        m = re.match(r'if /i "([^"]*)"=="([^"]*)" goto (\w+)$', s, re.I)
        if m:
            if m.group(1).upper() == m.group(2).upper():
                i = POS[m.group(3)]; seen.append(m.group(3))
            continue
        m = re.match(r'if not errorlevel 1 set "(\w+)=(.+)"$', s, re.I)
        if m:
            if ef == 0:
                env[m.group(1)] = m.group(2).strip('"')
            continue
        m = re.match(r'if (?:(not) )?errorlevel 1 goto (\w+)$', s, re.I)
        if m:
            if (ef == 0) == bool(m.group(1)):
                i = POS[m.group(2)]; seen.append(m.group(2))
            continue
        m = re.match(r'if "([^"]*)"=="([^"]*)" goto (\w+)$', s, re.I)
        if m:
            if m.group(1) == m.group(2):
                i = POS[m.group(3)]; seen.append(m.group(3))
            continue
        m = re.match(r'goto :?(\w+)$', s, re.I)
        if m:
            i = POS[m.group(1)]; seen.append(m.group(1)); continue
        if lo.startswith("exit /b"):
            break
    return (seen[-1] if seen else "?"), pulls


CASES = [
    ("已有模型", dict(has_model=1, ollama_up=1, llama_up=0, cli=1, mem=8, ans="N"),
     "MODEL_READY", []),
    ("无 Ollama", dict(has_model=0, ollama_up=0, llama_up=0, cli=0, mem=8, ans="N"),
     "MODEL_NO_OLLAMA", []),
    ("已装未启动答Y", dict(has_model=0, ollama_up=0, llama_up=0, cli=1, mem=8, ans="Y"),
     "MODEL_DO_PULL", ["qwen2.5:3b"]),
    ("运行中 8G 答Y", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=8, ans="Y"),
     "MODEL_DO_PULL", ["qwen2.5:3b"]),
    ("运行中 16G 答Y", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=16, ans="Y"),
     "MODEL_DO_PULL", ["qwen2.5:7b"]),
    ("运行中 32G 答Y", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=32, ans="Y"),
     "MODEL_DO_PULL", ["qwen2.5:14b"]),
    ("运行中答 N", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=8, ans="N"),
     "MODEL_SKIP", []),
    ("运行中答空", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=8, ans=""),
     "MODEL_SKIP", []),
    ("运行中答小写y", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=8, ans="y"),
     "MODEL_DO_PULL", ["qwen2.5:3b"]),
    ("下载失败", dict(has_model=0, ollama_up=1, llama_up=0, cli=1, mem=8, ans="Y",
                 pull_ok=False), "MODEL_PULL_FAIL", ["qwen2.5:3b"]),
]


def main():
    ok = True
    for name, kw, exp_label, exp_pulls in CASES:
        got, pulls = run(**kw)
        good = got == exp_label and pulls == exp_pulls
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name:14s} -> {got:20s} pull={pulls}")
    print("\n全部通过" if ok else "\n存在失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
