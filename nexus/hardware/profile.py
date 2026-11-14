from nexus.hardware.detector import detect_hardware
from nexus.hardware.optimizer import optimize
import json

def get_profile():
    hardware = detect_hardware()
    profile = optimize(hardware)
    return {"hardware": hardware, "profile": profile}

if __name__ == "__main__":
    print(json.dumps(get_profile(), indent=2))
