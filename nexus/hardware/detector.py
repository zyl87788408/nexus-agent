"""Hardware detection: CPU, RAM, GPU/VRAM and disk.

Detection is best-effort and never raises: a machine without ``nvidia-smi``
simply reports ``gpu: None`` instead of failing to start.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

_UNKNOWN_RAM_MB = 2048


def _run(cmd: list[str], timeout: float = 3) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_name() -> str:
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine() or "unknown"


def _cpu_cores() -> int:
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        return os.cpu_count() or 1


def _meminfo(field: str) -> int | None:
    if platform.system() != "Linux":
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(field):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _total_ram_mb() -> int:
    value = _meminfo("MemTotal:")
    if value:
        return value
    if platform.system() == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out and out.strip().isdigit():
            return int(out.strip()) // (1024 * 1024)
    if platform.system() == "Windows":
        out = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"])
        if out:
            numbers = [t for t in out.split() if t.isdigit()]
            if numbers:
                return int(numbers[0]) // (1024 * 1024)
    return _UNKNOWN_RAM_MB


def _available_ram_mb() -> int:
    value = _meminfo("MemAvailable:")
    return value if value else _total_ram_mb()


def _disk_free_gb(path: str = ".") -> float:
    try:
        return round(shutil.disk_usage(path).free / (1024 ** 3), 2)
    except OSError:
        return 0.0


def _detect_gpu() -> dict:
    """Return gpu / vram_mb / gpu_vendor / gpu_count."""
    result: dict = {"gpu": None, "vram_mb": None, "gpu_vendor": None, "gpu_count": 0}

    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run([smi, "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits"])
        if out:
            parsed = []
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    parsed.append((parts[0], int(parts[1])))
            if parsed:
                result.update(
                    gpu=parsed[0][0],
                    vram_mb=max(vram for _, vram in parsed),
                    gpu_count=len(parsed),
                    gpu_vendor="nvidia",
                )
                return result

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        out = _run(["system_profiler", "SPDisplaysDataType"])
        if out and "Apple" in out:
            # Unified memory: total RAM is addressable by the GPU; the budget
            # logic still reserves the OS share separately.
            result.update(
                gpu="Apple Silicon GPU",
                gpu_vendor="apple",
                gpu_count=1,
                vram_mb=_total_ram_mb(),
            )
            return result

    rocm = shutil.which("rocm-smi")
    if rocm and (_run([rocm, "--showproductname"]) or ""):
        result.update(gpu="AMD GPU (ROCm)", gpu_vendor="amd", gpu_count=1)
    return result


def detect_hardware(path: str = ".") -> dict:
    return {
        "os": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu": _cpu_name(),
        "cpu_cores": _cpu_cores(),
        "ram_mb": _total_ram_mb(),
        "ram_available_mb": _available_ram_mb(),
        "disk_free_gb": _disk_free_gb(path),
        **_detect_gpu(),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(detect_hardware(), indent=2, ensure_ascii=False))
