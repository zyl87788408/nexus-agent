class PermissionManager:
    def __init__(self):
        self.grants = {}

    def grant(self, plugin, permissions):
        allowed = {"filesystem", "model", "tools", "memory"}
        if not set(permissions).issubset(allowed):
            raise ValueError("Invalid permission")
        self.grants[plugin] = set(permissions)

    def revoke(self, plugin):
        self.grants.pop(plugin, None)

    def check(self, plugin, permission):
        return permission in self.grants.get(plugin, set())

    def list_permissions(self, plugin):
        return sorted(self.grants.get(plugin, set()))
