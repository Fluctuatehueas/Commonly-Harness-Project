"""生命周期与进程内适配器测试（不启动真实子进程）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus.errors import PluginError, PluginNotReady  # noqa: E402
from plugin_bus.lifecycle import PluginLifecycleManager  # noqa: E402
from plugin_bus.manifest import manifest_from_dict  # noqa: E402
from plugin_bus.registry import CapabilityRegistry  # noqa: E402
from plugin_bus.storage import InstalledPlugin  # noqa: E402


def _manifest(capabilities: list[str] | None = None):
    return manifest_from_dict(
        {
            "plugin_id": "demo",
            "name": "demo",
            "version": "1.0.0",
            "protocolVersion": "1.0",
            "author": {"name": "tester"},
            "capabilities": capabilities or ["tool", "hook"],
            "runtime": {"kind": "subprocess", "command": "python"},
            "transport": {"kind": "stdio"},
            "permissions": [],
            "harnessVersion": {"min": "1.1.0", "max": "1.x"},
        },
        harness_version="1.1.0",
    )


class FakePlugin:
    def __init__(self, *, capabilities=None):
        self.capabilities = capabilities or {
            "tool": [{"name": "echo", "description": "", "inputSchema": {}}],
            "hook": [{"name": "before_tool", "priority": 10}],
        }

    async def initialize(self, params):
        return {"ok": True}

    async def describe(self, params):
        return {"plugin_id": "demo", "protocolVersion": "1.0", "capabilities": self.capabilities}

    async def tool_call(self, params):
        return {"content": f"echo:{params.get('name')}"}

    async def hook_invoke(self, params):
        return {"allow": True}

    async def health(self, params):
        return {"ok": True}

    async def shutdown(self, params):
        return {}


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.registry = CapabilityRegistry()
        self.manager = PluginLifecycleManager(
            user_id="u1",
            store=None,
            registry=self.registry,
            auto_disable_after=2,
        )

    def _install(self, manifest, target):
        self.manager.set_installed(
            {
                manifest.plugin_id: InstalledPlugin(
                    plugin_id=manifest.plugin_id,
                    version=manifest.version,
                    path=Path(self.temporary.name),
                    source="user",
                    manifest=manifest,
                )
            }
        )
        self.manager.register_inprocess(manifest.plugin_id, target)

    async def test_start_call_and_disable(self):
        manifest = _manifest()
        self._install(manifest, FakePlugin())
        snapshot = await self.manager.start("demo")
        self.assertEqual(len(snapshot.tools), 1)
        text = await self.manager.call_tool("demo", "echo", {"value": 1})
        self.assertEqual(text, "echo:echo")
        hook = await self.manager.invoke_hook("demo", "before_tool", {"tool": "plugin__demo__echo"})
        self.assertEqual(hook, {"allow": True})
        await self.manager.mark_disabled("demo")
        self.assertEqual(self.manager.state("demo").status, "disabled")
        with self.assertRaises(PluginNotReady):
            await self.manager.start("demo")

    async def test_capability_mismatch_marks_unhealthy(self):
        manifest = _manifest(["tool"])
        self._install(manifest, FakePlugin(capabilities={"ui": [{"slot": "chat.sidebar"}]}))
        with self.assertRaises(PluginError):
            await self.manager.start("demo")
        self.assertEqual(self.manager.state("demo").status, "unhealthy")

    async def test_restart_force_from_disabled(self):
        manifest = _manifest()
        self._install(manifest, FakePlugin())
        await self.manager.start("demo")
        await self.manager.mark_disabled("demo")
        snapshot = await self.manager.start("demo", force=True)
        self.assertEqual(snapshot.plugin_id, "demo")

    async def test_unknown_tool_rejected(self):
        manifest = _manifest()
        self._install(manifest, FakePlugin())
        await self.manager.start("demo")
        with self.assertRaises(PluginError):
            await self.manager.call_tool("demo", "missing", {})

    async def test_real_stdio_plugin_with_host_storage(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "fake_stdio_plugin.py"
        manifest = manifest_from_dict(
            {
                "plugin_id": "fixture",
                "name": "fixture",
                "version": "1.0.0",
                "protocolVersion": "1.0",
                "author": {"name": "tester"},
                "capabilities": ["tool"],
                "runtime": {
                    "kind": "subprocess",
                    "command": sys.executable,
                    "args": [str(fixture)],
                },
                "transport": {"kind": "stdio"},
                "permissions": ["storage"],
                "harnessVersion": {"min": "1.1.0", "max": "1.x"},
            },
            harness_version="1.1.0",
        )
        self._install(manifest, None)
        # 使用真实 stdio 传输，而不是进程内适配器。
        self.manager.inprocess_targets.pop("fixture", None)
        import agent.config as agent_config

        with patch.object(agent_config, "PLUGIN_USERS_ROOT", Path(self.temporary.name) / "users"):
            await self.manager.start("fixture")
            text = await self.manager.call_tool("fixture", "echo", {})
            self.assertEqual(text, "host-ok:True")

    async def asyncTearDown(self):
        await self.manager.shutdown()


if __name__ == "__main__":
    unittest.main()
