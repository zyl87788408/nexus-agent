from dataclasses import dataclass, field

@dataclass
class PluginManifest:
    name: str
    version: str
    host: str
    description: str = ""
    permissions: list = field(default_factory=list)

    def validate(self):
        if not self.name or not self.version or not self.host:
            raise ValueError("Missing plugin information")

        allowed = {"filesystem", "model", "tools", "memory"}
        if not set(self.permissions).issubset(allowed):
            raise ValueError("Invalid plugin permissions")

        return True

class NexusPlugin:
    manifest: PluginManifest

    def initialize(self, context):
        raise NotImplementedError

    def shutdown(self):
        pass
