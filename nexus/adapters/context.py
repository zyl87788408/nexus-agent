class PluginContext:
    def __init__(self, plugin_name, permission_manager, services=None):
        self.plugin_name = plugin_name
        self.permissions = permission_manager
        # Preserve the caller's mapping even when empty: plugins register into
        # it and expect the host to see those entries.
        self.services = services if services is not None else {}

    def require(self, permission):
        if not self.permissions.check(self.plugin_name, permission):
            raise PermissionError(
                f"{self.plugin_name} lacks permission: {permission}"
            )

    def call_service(self, permission, service, *args, **kwargs):
        self.require(permission)

        if service not in self.services:
            raise RuntimeError(f"Service unavailable: {service}")

        return self.services[service](*args, **kwargs)

    def available_permissions(self):
        return self.permissions.list_permissions(self.plugin_name)
