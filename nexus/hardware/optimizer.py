def optimize(hw):
    ram = hw["ram_mb"]
    cores = hw["cpu_cores"] or 1

    if ram < 4096:
        mode, agents = "Lite", 1
    elif ram < 8192:
        mode, agents = "Balanced", 2
    else:
        mode, agents = "Performance", min(4, cores)

    return {
        "mode": mode,
        "max_parallel_agents": agents,
        "ram_mb": ram,
        "cpu_cores": cores,
        "recommendation": (
            "Use small quantized local models"
            if ram < 8192 else "Select model according to available resources"
        )
    }
