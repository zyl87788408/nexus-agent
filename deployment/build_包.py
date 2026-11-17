#!/usr/bin/env python3
"""把 nexus/ + server.py 打成单文件 nexus.pyz（zipapp）。

用法:
    python3 build_包.py <源码目录> <输出目录>

产物:
    <输出目录>/nexus.pyz      单文件程序（替代 nexus/ 的 38 个 .py）
    <输出目录>/requirements.txt

为什么用 zipapp 而不是 PyInstaller:
    PyInstaller 只能在本平台产出可执行文件，Linux 上打不出 Windows exe，
    且体积会涨到 20MB+。zipapp 是标准库能力，一次打包三平台通用，
    客户端本来就必须装 Python（部署脚本已强制检查 3.10+）。
"""

import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

PYZ_NAME = "nexus.pyz"

# 这些目录不参与打包：缓存、测试、本地运行态数据
EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "tests", "data", "out", "plugins"}


def build(src: Path, out: Path) -> Path:
    pyz = out / PYZ_NAME
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()

        # server.py 作为 zipapp 入口，必须叫 __main__.py
        shutil.copy2(src / "server.py", stage / "__main__.py")

        shutil.copytree(
            src / "nexus",
            stage / "nexus",
            ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc"),
        )

        zipapp.create_archive(
            stage,
            target=pyz,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )

    pyz.chmod(0o755)

    req = src / "requirements.txt"
    if req.is_file():
        shutil.copy2(req, out / "requirements.txt")

    return pyz


def verify(pyz: Path) -> bool:
    """确认包内 nexus 可被导入，且入口存在。"""
    code = (
        "import zipfile,sys;"
        "z=zipfile.ZipFile(sys.argv[1]);"
        "n=z.namelist();"
        "sys.exit(0 if '__main__.py' in n and 'nexus/config.py' in n "
        "and 'nexus/models/registry.py' in n else 1)"
    )
    return subprocess.run([sys.executable, "-c", code, str(pyz)]).returncode == 0


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if not (src / "server.py").is_file():
        print(f"[错误] {src} 里没有 server.py")
        return 1

    out.mkdir(parents=True, exist_ok=True)
    pyz = build(src, out)

    if not verify(pyz):
        print("[错误] 打包产物缺少关键模块")
        return 1

    kb = pyz.stat().st_size / 1024
    print(f"已生成 {pyz}  ({kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
