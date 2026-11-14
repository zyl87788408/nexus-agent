"""Plugin system: discovery, real loading, permissions, lifecycle, isolation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.adapters.loader import PluginLoadError, PluginLoader
from nexus.adapters.manager import PluginManager
from nexus.adapters.permissions import PermissionManager


def write_plugin(root: Path, name: str, manifest: dict, source: str | None = None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    if source is not None:
        (directory / "plugin.py").write_text(source, "utf-8")
    return directory


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_manifest_is_discovered(self):
        write_plugin(self.root, "good", {
            "name": "good", "version": "1.0", "host": "*",
            "permissions": ["tools"]})
        names = [p["name"] for p in PluginLoader(self.root).discover()]
        self.assertEqual(names, ["good"])

    def test_missing_required_field_is_skipped(self):
        write_plugin(self.root, "bad", {"name": "bad", "version": "1.0"})
        self.assertEqual(PluginLoader(self.root).discover(), [])

    def test_unknown_permission_is_skipped(self):
        write_plugin(self.root, "evil", {
            "name": "evil", "version": "1.0", "host": "*",
            "permissions": ["root"]})
        self.assertEqual(PluginLoader(self.root).discover(), [])

    def test_corrupt_manifest_is_skipped(self):
        directory = self.root / "broken"
        directory.mkdir()
        (directory / "manifest.json").write_text("{not json", "utf-8")
        self.assertEqual(PluginLoader(self.root).discover(), [])

    def test_non_list_permissions_is_skipped(self):
        write_plugin(self.root, "weird", {
            "name": "weird", "version": "1.0", "host": "*",
            "permissions": "tools"})
        self.assertEqual(PluginLoader(self.root).discover(), [])

    def test_missing_directory_yields_empty_list(self):
        self.assertEqual(PluginLoader(self.root / "absent").discover(), [])


class LoadingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plugin_module_is_really_imported_and_setup_runs(self):
        write_plugin(self.root, "hello", {
            "name": "hello", "version": "1.0", "host": "*",
            "permissions": ["tools"]},
            source=(
                "def setup(ctx):\n"
                "    ctx.services['loaded'] = ctx.plugin_name\n"
                "def greet(name):\n"
                "    return f'hello {name}'\n"
            ))
        manager = PluginManager(self.root, services={})
        manager.activate("hello")
        self.assertEqual(manager.services["loaded"], "hello")
        self.assertEqual(manager.call("hello", "greet", "nexus"), "hello nexus")

    def test_manifest_only_plugin_activates_without_module(self):
        write_plugin(self.root, "declarative", {
            "name": "declarative", "version": "1.0", "host": "*",
            "permissions": []})
        manager = PluginManager(self.root, services={})
        manager.activate("declarative")
        self.assertIn("declarative", manager.modules)

    def test_broken_setup_is_isolated(self):
        write_plugin(self.root, "broken", {
            "name": "broken", "version": "1.0", "host": "*",
            "permissions": []},
            source="def setup(ctx):\n    raise RuntimeError('boom')\n")
        manager = PluginManager(self.root, services={})
        manager.activate("broken")
        self.assertNotIn("broken", manager.modules)
        self.assertIn("boom", manager.errors["broken"])

    def test_syntax_error_is_isolated(self):
        write_plugin(self.root, "syntax", {
            "name": "syntax", "version": "1.0", "host": "*",
            "permissions": []},
            source="def broken(:\n")
        manager = PluginManager(self.root, services={})
        manager.activate("syntax")
        self.assertIn("syntax", manager.errors)

    def test_one_broken_plugin_does_not_break_another(self):
        write_plugin(self.root, "broken", {
            "name": "broken", "version": "1.0", "host": "*",
            "permissions": []}, source="raise RuntimeError('bad')\n")
        write_plugin(self.root, "good", {
            "name": "good", "version": "1.0", "host": "*",
            "permissions": []},
            source="def ping():\n    return 'pong'\n")
        manager = PluginManager(self.root, services={})
        manager.activate("broken")
        manager.activate("good")
        self.assertEqual(manager.call("good", "ping"), "pong")

    def test_call_on_inactive_plugin_raises(self):
        write_plugin(self.root, "idle", {
            "name": "idle", "version": "1.0", "host": "*",
            "permissions": []})
        manager = PluginManager(self.root, services={})
        with self.assertRaises(PluginLoadError):
            manager.call("idle", "anything")

    def test_call_missing_function_raises(self):
        write_plugin(self.root, "p", {"name": "p", "version": "1.0",
                                      "host": "*", "permissions": []},
                     source="def exists():\n    return 1\n")
        manager = PluginManager(self.root, services={})
        manager.activate("p")
        with self.assertRaises(PluginLoadError):
            manager.call("p", "missing")

    def test_shutdown_runs_on_deactivate(self):
        marker = self.root / "shutdown-marker.txt"
        write_plugin(self.root, "lifecycle", {
            "name": "lifecycle", "version": "1.0", "host": "*",
            "permissions": []},
            source=(
                "def setup(ctx):\n    pass\n"
                f"def shutdown():\n"
                f"    with open({str(marker)!r}, 'w') as fh:\n"
                f"        fh.write('done')\n"
            ))
        manager = PluginManager(self.root, services={})
        manager.activate("lifecycle")
        manager.deactivate("lifecycle")
        self.assertTrue(marker.exists())


class PermissionTest(unittest.TestCase):
    def test_grants_follow_the_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_plugin(root, "p", {"name": "p", "version": "1.0",
                                     "host": "*", "permissions": ["memory"]})
            manager = PluginManager(root, services={})
            manager.activate("p")
            self.assertTrue(manager.permissions.check("p", "memory"))
            self.assertFalse(manager.permissions.check("p", "filesystem"))

    def test_revoke_clears_permissions(self):
        manager = PermissionManager()
        manager.grant("p", ["tools"])
        manager.revoke("p")
        self.assertFalse(manager.check("p", "tools"))

    def test_invalid_permission_is_rejected(self):
        with self.assertRaises(ValueError):
            PermissionManager().grant("p", ["root"])

    def test_context_enforces_permission(self):
        from nexus.adapters.context import PluginContext

        manager = PermissionManager()
        manager.grant("p", ["memory"])
        context = PluginContext("p", manager, {"get": lambda: 42})
        self.assertEqual(context.call_service("memory", "get"), 42)
        with self.assertRaises(PermissionError):
            context.call_service("tools", "get")

    def test_missing_service_raises(self):
        from nexus.adapters.context import PluginContext

        manager = PermissionManager()
        manager.grant("p", ["memory"])
        context = PluginContext("p", manager, {})
        with self.assertRaises(RuntimeError):
            context.call_service("memory", "absent")

    def test_host_adapter_contract_is_abstract(self):
        from nexus.adapters.base import HostAdapter

        with self.assertRaises(TypeError):
            HostAdapter()


class StatusTest(unittest.TestCase):
    def test_status_reports_the_whole_picture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_plugin(root, "ok", {"name": "ok", "version": "1.0",
                                      "host": "*", "permissions": []})
            write_plugin(root, "bad", {"name": "bad", "version": "1.0",
                                       "host": "*", "permissions": []},
                         source="raise RuntimeError('x')\n")
            manager = PluginManager(root, services={})
            manager.activate("ok")
            manager.activate("bad")
            status = manager.status()
            self.assertIn("ok", status["active"])
            self.assertIn("bad", status["errors"])
            self.assertEqual(sorted(status["discovered"]), ["bad", "ok"])

    def test_uninstall_removes_everything(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_plugin(root, "p", {"name": "p", "version": "1.0",
                                     "host": "*", "permissions": ["tools"]})
            manager = PluginManager(root, services={})
            manager.activate("p")
            manager.uninstall("p")
            self.assertNotIn("p", manager.modules)
            self.assertNotIn("p", manager.lifecycle.plugins)
            self.assertEqual(manager.permissions.list_permissions("p"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
