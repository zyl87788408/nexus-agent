"""Plugin host: ties discovery, lifecycle, permissions and tools together.

This is the piece that was missing - previously the adapter modules existed but
nothing ever loaded or called a plugin.
"""

from __future__ import annotations

from nexus.adapters.context import SERVICE_PERMISSIONS, PluginContext
from nexus.adapters.lifecycle import PluginLifecycle
from nexus.adapters.loader import PluginLoadError, PluginLoader
from nexus.adapters.permissions import PermissionManager


class PluginManager:
    def __init__(self, plugin_dir="plugins", services: dict | None = None):
        self.loader = PluginLoader(plugin_dir)
        self.lifecycle = PluginLifecycle()
        self.permissions = PermissionManager()
        self.services = services or {}
        unmapped = sorted(set(self.services) - set(SERVICE_PERMISSIONS))
        if unmapped:
            # Exposing a service with no declared permission requirement would
            # hand it to every plugin regardless of manifest. Refuse early.
            raise ValueError(
                f"plugin service(s) {unmapped} have no permission mapping in "
                f"SERVICE_PERMISSIONS; declare a requirement or stop exposing them"
            )
        self.modules: dict[str, object] = {}
        self.errors: dict[str, str] = {}

    # ------------------------------------------------------------ management
    def install(self, name: str) -> dict:
        """Register a discovered plugin and grant its declared permissions."""
        manifest = self.loader.get(name)
        if manifest is None:
            raise PluginLoadError(f"plugin {name!r} not found or manifest invalid")
        if name not in self.lifecycle.plugins:
            self.lifecycle.install(name, manifest)
        self.permissions.grant(name, manifest.get("permissions", []))
        return manifest

    def activate(self, name: str):
        """Install (if needed) then import and run setup()."""
        manifest = self.install(name)
        self.lifecycle.enable(name)
        context = PluginContext(name, self.permissions, self.services)
        registry = self.services.get("tools")
        trackable = registry is not None and hasattr(registry, "names")
        before = set(registry.names()) if trackable else set()
        try:
            module = self.loader.load(name, context)
        except PluginLoadError as exc:
            # A setup() that fails halfway has already registered whatever it
            # managed to add. Leaving those behind means an inert plugin still
            # contributes callable tools, so undo everything it added.
            if trackable:
                for tool in set(registry.names()) - before:
                    registry.unregister(tool)
            self.permissions.revoke(name)
            self.errors[name] = str(exc)
            self.lifecycle.disable(name)
            return None
        self.modules[name] = module
        self.errors.pop(name, None)
        return module

    def activate_all(self) -> dict[str, object]:
        """Activate every discoverable plugin.

        Discovery alone does nothing; the engine used to construct the host and
        never call activate(), so a plugin placed in `plugins/` stayed inert
        while /api/plugins reported it as discovered. Failures are contained per
        plugin, so one bad plugin neither stops the others nor the engine.
        """
        started = {}
        for name in self.discover():
            module = self.activate(name)
            if module is not None:
                started[name] = module
        return started

    def deactivate(self, name: str) -> None:
        module = self.modules.pop(name, None)
        if module is not None:
            self.loader.unload(module)
        self.lifecycle.disable(name)

    def uninstall(self, name: str) -> None:
        self.deactivate(name)
        self.lifecycle.uninstall(name)
        self.permissions.revoke(name)
        self.modules.pop(name, None)
        self.errors.pop(name, None)

    # ---------------------------------------------------------------- invokе
    def call(self, plugin: str, function: str, /, *args, **kwargs):
        """Call a function exposed by an activated plugin module."""
        module = self.modules.get(plugin)
        if module is None:
            raise PluginLoadError(f"plugin {plugin!r} is not active")
        fn = getattr(module, function, None)
        if not callable(fn):
            raise PluginLoadError(f"plugin {plugin!r} exposes no {function!r}()")
        return fn(*args, **kwargs)

    def call_tool(self, plugin: str, tool: str, /, **kwargs):
        """Invoke a tool registered by a plugin through its context services."""
        service = self.services.get("call_tool")
        if service is None:
            raise PluginLoadError("no tool service wired into the plugin host")
        return service(tool, **kwargs)

    # ----------------------------------------------------------------- state
    def discover(self) -> list[str]:
        return [p["name"] for p in self.loader.discover()]

    def status(self) -> dict:
        _, skipped = self.loader.discover_detailed()
        errors = dict(self.errors)
        # Skipped directories are reported under the directory name so a broken
        # manifest is visible instead of silently absent from the list.
        for directory, reason in skipped.items():
            errors.setdefault(directory, reason)
        return {
            "discovered": self.discover(),
            "installed": sorted(self.lifecycle.plugins),
            "active": sorted(self.modules),
            "enabled": self.lifecycle.status(),
            "errors": errors,
        }
