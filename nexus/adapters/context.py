"""Services a plugin may reach, gated per capability.

`services` used to be a plain dict, so declaring `"permissions": []` still let a
plugin grab `context.services["tools"]` and register tools. The permission
scopes were recorded and documented but never consulted on that path.
"""

from collections.abc import Mapping

# Which declared permission each built-in capability service requires.
# Every service the host exposes must appear here. A service that is missing
# from this map would be readable by a plugin holding no permissions at all, so
# PluginManager refuses to start with an unmapped service rather than exposing
# it by accident.
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
        if key not in SERVICE_PERMISSIONS:
            # Fail closed: an unmapped service has no declared requirement, and
            # guessing "no requirement" would defeat the gate.
            raise PermissionError(
                f"service {key!r} is not available to plugins "
                f"(no permission mapping declared for it)"
            )
        required = SERVICE_PERMISSIONS[key]
        if not self._permissions.check(self._plugin_name, required):
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


class OwnedToolRegistry:
    """Per-plugin view of the host tool registry: write only what you registered.

    Handing plugins the raw registry let `register(..., replace=True)` swap out a
    host tool. Verified: a plugin with only the `tools` permission replaced
    run_shell's handler and rewrote its scope from "shell" to "read", so shell work
    then ran without the shell scope ever being granted. Ownership tracking is
    what makes removal on deactivate both possible and safe.
    """

    def __init__(self, plugin_name: str, registry, owned: set):
        self._plugin_name = plugin_name
        self._registry = registry
        self._owned = owned

    def register(self, spec, *, replace: bool = False) -> None:
        name = getattr(spec, "name", None)
        if self._registry.get(name) is not None and name not in self._owned:
            raise PermissionError(
                f"{self._plugin_name} cannot replace host tool {name!r}; "
                f"pick a different name"
            )
        # Replacing a tool this plugin registered is legitimate (upgrade in place).
        self._registry.register(spec, replace=True)
        self._owned.add(name)

    def unregister(self, name: str) -> bool:
        if name not in self._owned:
            raise PermissionError(
                f"{self._plugin_name} did not register tool {name!r}"
            )
        self._owned.discard(name)
        return self._registry.unregister(name)

    def __getattr__(self, item):
        # Reads (names, specs, get, sandbox, ...) go straight to the host registry.
        return getattr(self._registry, item)


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
        """Invoke a named service after checking an explicit permission.

        Unlike `self.services[...]`, which gates by capability name, this path
        takes the required permission from the caller and verifies it here, so
        service names are free-form. The two paths stay separate: routing this
        one through SERVICE_PERMISSIONS would reject names that are not
        capabilities.
        """
        self.require(permission)
        try:
            fn = self._services[service]
        except KeyError:
            raise RuntimeError(f"Service unavailable: {service}") from None

        return fn(*args, **kwargs)

    def available_permissions(self):
        return self.permissions.list_permissions(self.plugin_name)
