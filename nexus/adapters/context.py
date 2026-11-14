"""Services a plugin may reach, gated per capability.

`services` used to be a plain dict, so declaring `"permissions": []` still let a
plugin grab `context.services["tools"]` and register tools. The permission
scopes were recorded and documented but never consulted on that path.
"""

from collections.abc import Mapping

# Which declared permission each built-in capability service requires.
SERVICE_PERMISSIONS = {
    "tools": "tools",
    "call_tool": "tools",
    "memory": "memory",
    "model": "model",
    "chat": "model",
    "filesystem": "filesystem",
}


class GatedServices(Mapping):
    """Read-only, permission-checked view over the host service mapping."""

    def __init__(self, plugin_name: str, permissions, services: dict):
        self._plugin_name = plugin_name
        self._permissions = permissions
        self._services = services

    def _check(self, key: str) -> None:
        required = SERVICE_PERMISSIONS.get(key)
        if required and not self._permissions.check(self._plugin_name, required):
            raise PermissionError(
                f"{self._plugin_name} lacks permission: {required} (needed for service {key!r})"
            )

    def __getitem__(self, key):
        self._check(key)
        return self._services[key]

    def __iter__(self):
        return iter(self._services)

    def __len__(self):
        return len(self._services)

    def __contains__(self, key):
        return key in self._services

    def __setitem__(self, key, value):
        # Host-owned registry: plugins mutate tools through the service itself,
        # not by swapping host entries out from under each other.
        raise TypeError("plugin service mapping is read-only")

    def __repr__(self):
        return f"GatedServices({self._plugin_name!r}, {sorted(self._services)})"


class PluginContext:
    def __init__(self, plugin_name, permission_manager, services=None):
        self.plugin_name = plugin_name
        self.permissions = permission_manager
        # Preserve the caller's mapping even when empty: plugins register into
        # it and expect the host to see those entries. Access goes through the
        # permission gate so declared scopes are actually enforced.
        self._services = services if services is not None else {}
        self.services = GatedServices(plugin_name, permission_manager, self._services)

    def require(self, permission):
        if not self.permissions.check(self.plugin_name, permission):
            raise PermissionError(
                f"{self.plugin_name} lacks permission: {permission}"
            )

    def call_service(self, permission, service, *args, **kwargs):
        self.require(permission)

        # `self.services` re-checks the capability's own permission on access.
        try:
            fn = self.services[service]
        except KeyError:
            raise RuntimeError(f"Service unavailable: {service}") from None

        return fn(*args, **kwargs)

    def available_permissions(self):
        return self.permissions.list_permissions(self.plugin_name)
