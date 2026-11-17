"""NEXUS 悬浮窗 — 三键切换模型来源。

无边框、始终置顶、可拖动。只依赖标准库（tkinter/urllib），
不引入任何第三方 GUI 依赖。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import tkinter as tk
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8085"

BG = "#12141a"
CARD = "#1b1e27"
LINE = "#2b3040"
TXT = "#e6e8ee"
DIM = "#8b93a7"
LOCAL = "#2ecc71"
API = "#4a9eff"
AUTO = "#b46cff"
ERR = "#e5484d"
WARN = "#f5a524"

MODES = [
    ("offline", "本地", LOCAL, "只用自己的电脑，不联网"),
    ("auto", "自动", AUTO, "优先本地，不可用时切 API"),
    ("online", "API", API, "始终用 API，需要联网"),
]


def _short(text: str, limit: int = 26) -> str:
    """把后端返回的英文错误压缩成一行可读的中文提示。"""
    low = text.lower()
    if "not enabled" in low:
        return "未配置 API，请先在面板设置"
    if "api key" in low:
        return "未填写 API 密钥"
    if "not authorized" in low:
        return "API 未授权"
    if "not found" in low or "not available" in low:
        return "模型不存在或不可用"
    return text[:limit] + ("…" if len(text) > limit else "")


class Panel:
    def __init__(self) -> None:
        self.base = self._read_base()
        self.root = tk.Tk()
        self.root.title("NEXUS")
        self.root.overrideredirect(True)          # 无边框
        self.root.attributes("-topmost", True)    # 始终置顶
        self.root.configure(bg=BG)

        w, h = 244, 168
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{sw - w - 28}+{sh // 3}")

        self._build()
        self._drag_hook()
        self.refresh()
        self.root.after(5000, self._tick)

    # ---------------------------------------------------------------- setup
    def _read_base(self) -> str:
        """优先环境变量；否则读 data/config.json 里用户改过的端口。

        端口在 ④ 常用工具 里可以改，写死 8085 会让悬浮窗连不上。
        """
        import os
        override = os.environ.get("NEXUS_PANEL_URL")
        if override:
            return override.rstrip("/")

        here = Path(__file__).resolve().parent
        cfg_path = here / "data" / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
            port = int((cfg.get("server") or {}).get("port") or 0)
            host = (cfg.get("server") or {}).get("host") or "127.0.0.1"
            # 0.0.0.0 是监听地址，不是可连接地址
            if host in {"0.0.0.0", "::", ""}:
                host = "127.0.0.1"
            if port:
                return f"http://{host}:{port}"
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass
        return DEFAULT_BASE

    def _build(self) -> None:
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=10, pady=(8, 4))

        tk.Label(head, text="NEXUS", bg=BG, fg=TXT,
                 font=("Microsoft YaHei", 9, "bold")).pack(side="left")
        self.status = tk.Label(head, text="…", bg=BG, fg=DIM,
                               font=("Microsoft YaHei", 8))
        self.status.pack(side="left", padx=(6, 0))
        close = tk.Label(head, text="X", bg=BG, fg=DIM,
                         font=("Segoe UI", 9), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.root.destroy())

        self.btns: dict[str, tk.Label] = {}
        for key, label, colour, tip in MODES:
            f = tk.Frame(self.root, bg=CARD, highlightbackground=LINE,
                         highlightthickness=1)
            f.pack(fill="x", padx=10, pady=2)
            lbl = tk.Label(f, text=label, bg=CARD, fg=TXT,
                           font=("Microsoft YaHei", 9), anchor="w",
                           padx=8, pady=5, cursor="hand2")
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Button-1>", lambda e, k=key: self.switch(k))
            f.bind("<Button-1>", lambda e, k=key: self.switch(k))
            self.btns[key] = (f, lbl, colour)

        self.detail = tk.Label(self.root, text="", bg=BG, fg=DIM,
                               font=("Microsoft YaHei", 7), anchor="w")
        self.detail.pack(fill="x", padx=12, pady=(4, 8))

    def _drag_hook(self) -> None:
        for widget in (self.root,):
            widget.bind("<Button-1>", self._press)
            widget.bind("<B1-Motion>", self._drag)

    def _press(self, event) -> None:
        self._x, self._y = event.x, event.y

    def _drag(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._x}+{event.y_root - self._y}")

    # ------------------------------------------------------------------ api
    def _call(self, path: str, body: dict | None = None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())

    def switch(self, mode: str) -> None:
        body = {"mode": mode, "persist": True,
                "allow_cloud": mode != "offline"}
        try:
            result = self._call("/api/mode", body)
        except Exception as exc:
            self.detail.config(text=f"切换失败：{exc}", fg=ERR)
            return

        # 模式本身切过去了，但后端没验证通过（如 API 未配置）。
        # 必须说出来，否则用户看到一个"选中了却不能用"的状态。
        if not result.get("verified"):
            reason = (result.get("detail") or {}).get("error") or "后端不可用"
            self.detail.config(text=f"!! {_short(reason)}", fg=WARN)
        self.refresh()

    def refresh(self) -> None:
        try:
            m = self._call("/api/modes")
            s = self._call("/api/status")
        except Exception:
            self.status.config(text="未连接", fg=ERR)
            self.detail.config(text=f"服务未响应 {self.base}", fg=ERR)
            return

        cur = m.get("current", "")
        for key, (frame, lbl, colour) in self.btns.items():
            on = key == cur
            frame.config(highlightbackground=colour if on else LINE)
            lbl.config(fg=colour if on else TXT,
                       font=("Microsoft YaHei", 9, "bold" if on else "normal"))

        src = s.get("active_source", "")
        names = {"local": "本地", "cloud": "API", "simulated": "未就绪"}
        self.status.config(text=names.get(src, src), fg=DIM)

        model = s.get("active_model") or "—"
        if s.get("simulated"):
            self.detail.config(text="!! 无可用模型", fg=WARN)
        else:
            self.detail.config(text=f"{model}", fg=DIM)

    def _tick(self) -> None:
        self.refresh()
        self.root.after(5000, self._tick)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    try:
        Panel().run()
    except Exception as exc:
        print(f"悬浮窗启动失败：{exc}", file=sys.stderr)
        sys.exit(1)
