import json
from pathlib import Path

class PluginLoader:
    def __init__(self, plugin_dir="plugins"):
        self.plugin_dir = Path(plugin_dir).resolve()
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

    def discover(self):
        plugins = []

        for manifest in self.plugin_dir.glob("*/manifest.json"):
            try:
                data = json.loads(manifest.read_text())
                required = ("name", "version", "host")

                if not all(data.get(key) for key in required):
                    continue

                permissions = data.get("permissions", [])
                allowed = {"filesystem", "model", "tools", "memory"}

                if not isinstance(permissions, list):
                    continue
                if not set(permissions).issubset(allowed):
                    continue

                plugins.append(data)

            except (OSError, ValueError):
                continue

        return plugins
