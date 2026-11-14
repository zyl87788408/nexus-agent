class PluginLifecycle:
    def __init__(self):
        self.plugins = {}

    def install(self, name, manifest):
        if name in self.plugins:
            raise ValueError("Plugin already installed")
        self.plugins[name] = {
            "manifest": manifest,
            "enabled": False
        }

    def enable(self, name):
        if name not in self.plugins:
            raise KeyError(name)
        self.plugins[name]["enabled"] = True

    def disable(self, name):
        if name not in self.plugins:
            raise KeyError(name)
        self.plugins[name]["enabled"] = False

    def uninstall(self, name):
        self.plugins.pop(name, None)

    def status(self):
        return {
            name: data["enabled"]
            for name, data in self.plugins.items()
        }
