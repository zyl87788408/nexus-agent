"""Discover and actually load plugins from disk.

A plugin is a directory containing:
    manifest.json   {"name", "version", "host", "permissions": [...]}
    plugin.py       optional; may define setup(context) -> None / shutdown()

Discovery only parses manifests. Loading gives each plugin its own module name
so imports do not collide, and contains failures: a broken plugin yields an
error entry instead of an exception that could take down the engine. Plugins
run in-process and are trusted code; the permission scopes gate the host
services they can reach, they are not a sandbox.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ALLOWED_PERMISSIONS = {"filesystem", "model", "tools", "memory"}
REQUIRED_FIELDS = ("name", "version", "host")


class PluginLoadError(RuntimeError):
    pass


class PluginLoader:
    def __init__(self, plugin_dir="plugins"):
        self.plugin_dir = Path(plugin_dir).resolve()
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ discovery
    def discover(self) -> list[dict]:
        plugins = []
        for manifest in sorted(self.plugin_dir.glob("*/manifest.json")):
            entry = self._read_manifest(manifest)
            if entry:
                entry["path"] = str(manifest.parent)
                entry["module_path"] = str(manifest.parent / "plugin.py")
                plugins.append(entry)
        return plugins

    def _read_manifest(self, manifest: Path) -> dict | None:
        try:
            data = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not all(data.get(key) for key in REQUIRED_FIELDS):
            return None
        permissions = data.get("permissions", [])
        if not isinstance(permissions, list):
            return None
        if not set(permissions).issubset(ALLOWED_PERMISSIONS):
            return None
        return data

    def get(self, name: str) -> dict | None:
        for plugin in self.discover():
            if plugin["name"] == name:
                return plugin
        return None

    # --------------------------------------------------------------- loading
    def load(self, name: str, context):
        """Import `<plugin>/plugin.py` and call its setup(context).

        Returns the imported module, or None when the plugin has no module.
        Raises PluginLoadError when the module exists but fails to initialise.
        """
        plugin = self.get(name)
        if plugin is None:
            raise PluginLoadError(f"plugin {name!r} not found")

        module_path = Path(plugin["module_path"])
        if not module_path.exists():
            return None

        spec = importlib.util.spec_from_file_location(
            f"nexus_plugin_{name}", module_path
        )
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"cannot import {module_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise PluginLoadError(f"{type(exc).__name__}: {exc}") from exc

        setup = getattr(module, "setup", None)
        if callable(setup):
            try:
                setup(context)
            except Exception as exc:
                raise PluginLoadError(
                    f"setup() failed: {type(exc).__name__}: {exc}"
                ) from exc
        return module

    def unload(self, module) -> None:
        shutdown = getattr(module, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
