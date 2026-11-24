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
import os
import stat
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
        return self.discover_detailed()[0]

    def discover_detailed(self) -> tuple[list[dict], dict[str, str]]:
        """Return (loadable plugins, {directory: reason it was skipped}).

        A rejected manifest used to disappear silently: the plugin simply was not
        in the list, with nothing in /api/plugins explaining why. Skipped
        directories are reported so a typo in a manifest is visible.
        """
        plugins: list[dict] = []
        errors: dict[str, str] = {}
        seen: dict[str, str] = {}
        for manifest in sorted(self.plugin_dir.glob("*/manifest.json")):
            directory = manifest.parent.name
            entry, reason = self._read_manifest(manifest)
            if entry is None:
                errors[directory] = reason
                continue
            name = entry["name"]
            if name in seen:
                errors[directory] = (
                    f"duplicate plugin name {name!r}, already declared by {seen[name]!r}"
                )
                continue
            seen[name] = directory
            entry["path"] = str(manifest.parent)
            entry["module_path"] = str(manifest.parent / "plugin.py")
            plugins.append(entry)
        return plugins, errors

    def _read_manifest(self, manifest: Path) -> tuple[dict | None, str]:
        try:
            data = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            return None, f"manifest unreadable: {type(exc).__name__}: {exc}"
        if not isinstance(data, dict):
            return None, "manifest is not a JSON object"
        missing = [key for key in REQUIRED_FIELDS if not data.get(key)]
        if missing:
            return None, f"manifest missing required field(s): {missing}"
        permissions = data.get("permissions", [])
        if not isinstance(permissions, list):
            return None, "manifest 'permissions' must be a list"
        invalid = sorted(set(permissions) - ALLOWED_PERMISSIONS)
        if invalid:
            return None, f"manifest declares unknown permission(s): {invalid}"
        return data, ""

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

        self._check_ownership(module_path)

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
                # Give a partially-initialised plugin the chance to clean up
                # before the caller rolls back whatever it registered.
                self.unload(module)
                raise PluginLoadError(
                    f"setup() failed: {type(exc).__name__}: {exc}"
                ) from exc
        return module

    def _check_ownership(self, module_path: Path) -> None:
        """Refuse to import code another local user could have written.

        Loading a plugin runs it in-process, so its directory is a trust
        boundary. If that directory is group/world-writable, anyone on the
        machine can drop a module there and get code execution on the next
        start. Only the plugin's own directory is checked: its parents
        (``/tmp``, the home directory) are writable by design and say nothing
        about who controls this specific plugin.
        """
        if os.name != "posix":
            return
        st = module_path.parent.stat()
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PluginLoadError(
                f"{module_path.parent} is group/world-writable; refusing to "
                "load code from a directory other users can modify"
            )
        if st.st_uid != os.getuid():
            raise PluginLoadError(
                f"{module_path.parent} is owned by uid {st.st_uid}; "
                "expected the current user"
            )

    def unload(self, module) -> None:
        shutdown = getattr(module, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
