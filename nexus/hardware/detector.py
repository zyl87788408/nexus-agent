import os
import platform
import shutil

def detect_hardware():
    memory = 0
    cpu_model = platform.processor() or "Unknown"

    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    memory = int(line.split()[1]) // 1024
                    break

    if os.path.exists("/proc/cpuinfo"):
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break

    disk = shutil.disk_usage(".")
    return {
        "os": platform.system(),
        "architecture": platform.machine(),
        "cpu": cpu_model,
        "cpu_cores": os.cpu_count(),
        "ram_mb": memory,
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
    }

if __name__ == "__main__":
    import json
    print(json.dumps(detect_hardware(), indent=2))
