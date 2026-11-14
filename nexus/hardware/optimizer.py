"""Translate raw hardware into an execution profile.

The budget is derived from *actually available* memory minus a reserve for the
OS and Python runtime, instead of a flat percentage of total RAM. A flat 30% of
a 4 GB box leaves ~1.1 GB, which can never host even a small model - the exact
bug that made 4 GB machines unusable.
"""

from __future__ import annotations

DEFAULT_RESERVED_MB = 900
MIN_BUDGET_MB = 384

# Model weight budget -> context window actually affordable on CPU-only boxes.
_CONTEXT_LADDER = [(768, 2048), (1280, 4096), (2560, 8192), (10 ** 9, 16384)]


def _context_for(budget_mb: int) -> int:
    for limit, ctx in _CONTEXT_LADDER:
        if budget_mb <= limit:
            return ctx
    return 16384


def model_budget_mb(hw: dict, reserved_mb: int = DEFAULT_RESERVED_MB,
                    fraction: float = 1.0) -> int:
    """Memory available for model weights, never negative."""
    available = hw.get("ram_available_mb") or hw.get("ram_mb") or 2048
    total = hw.get("ram_mb") or available
    # Never budget more than the machine physically has, minus the reserve.
    ceiling = max(MIN_BUDGET_MB, int(total) - reserved_mb)
    usable = int(available) - reserved_mb
    if fraction < 1.0:
        usable = min(usable, int(total * fraction))
    return max(MIN_BUDGET_MB, min(ceiling, usable))


def optimize(hw: dict, reserved_mb: int = DEFAULT_RESERVED_MB) -> dict:
    ram = hw.get("ram_mb") or 2048
    available = hw.get("ram_available_mb") or ram
    cores = hw.get("cpu_cores") or 1
    vram = hw.get("vram_mb") or 0
    has_gpu = bool(vram)
    disk = hw.get("disk_free_gb") or 0

    budget = model_budget_mb(hw, reserved_mb)
    context = _context_for(budget)

    if ram < 4096:
        mode, agents, parallel = "Lite", 1, 1
    elif ram < 8192:
        mode, agents, parallel = "Balanced", 2, 1
    else:
        mode, agents, parallel = "Performance", min(4, max(1, cores)), 2

    # GPU present: the weights live in VRAM, so allow the larger end of the
    # ladder and more parallel work, bounded by what the card can hold.
    if has_gpu:
        gpu_budget = int(vram * 0.85)
        if gpu_budget > budget:
            budget = gpu_budget
            context = max(context, _context_for(gpu_budget))
        parallel = max(parallel, min(4, max(1, gpu_count := hw.get("gpu_count") or 1)))

    # Never promise a context window larger than the box can hold.
    context = min(context, max(512, budget * 2))

    return {
        "mode": mode,
        "max_parallel_agents": agents,
        "parallel_requests": parallel,
        "model_budget_mb": budget,
        "context_tokens": context,
        "has_gpu": has_gpu,
        "vram_mb": vram or None,
        "ram_mb": ram,
        "ram_available_mb": available,
        "cpu_cores": cores,
        "reserved_mb": reserved_mb,
        "low_resource": ram < 4096 or cores <= 2,
        "recommendation": _recommend(ram, budget, has_gpu, disk),
    }


def _recommend(ram: int, budget: int, has_gpu: bool, disk: float) -> str:
    if disk and disk < 3:
        return "Very low disk space: models will not fit; free space first"
    if has_gpu:
        return "GPU detected: prefer larger quantized models; weights fit in VRAM"
    if ram < 4096:
        return (f"Low memory: keep models under ~{budget} MB, prefer <=2B Q4 "
                "and short contexts")
    if ram < 8192:
        return f"Moderate memory: models up to ~{budget} MB are safe"
    return "Sufficient memory: larger models and longer contexts are viable"
