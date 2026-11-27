"""随包内置插件：sys_plugins 发现、入口加载与进程内运行。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol, storage  # noqa: E402
from plugin_bus.lifecycle import PluginLifecycleManager  # noqa: E402
from plugin_bus.manifest import load_manifest  # noqa: E402
from plugin_bus.registry import CapabilityRegistry  # noqa: E402
from plugin_bus.storage import InstalledPlugin  # noqa: E402
from plugin_bus.transport.inprocess import InProcessTransport, load_file_target  # noqa: E402

PLUGIN_ID = "builtin-demo"

PLUGIN_SOURCE = '''
def describe(params):
    return {
        "plugin_id": "builtin-demo",
        "protocolVersion": "1.0",
        "capabilities": {
            "tool": [
                {"name": "echo", "description": "内置回显", "inputSchema": {}}
            ]
        },
    }


def initialize(params):
    return {"ok": True}


def health(params):
    return {"ok": True}


def tool_call(params):
    return {"content": "sys:" + str((params.get("arguments") or {}).get("text", ""))}


def shutdown(params):
    return {"ok": True}


def handle(method, params):
    mapping = {
        "plugin.initialize": initialize,
        "plugin.describe": describe,
        "plugin.health": health,
        "tool.call": tool_call,
        "plugin.shutdown": shutdown,
    }
    return mapping[method](params)
'''


def _write_sys_plugin(root: Path) -> Path:
    plugin = root / PLUGIN_ID
    plugin.mkdir(parents=True)
    manifest = {
        "plugin_id": PLUGIN_ID,
        "name": "内置演示",
        "version": "1.0.0",
        "protocolVersion": "1.0",
        "author": {"name": "system"},
        "capabilities": ["tool"],
        "runtime": {"kind": "inprocess", "entry": "plugin:handle"},
        "transport": {"kind": "inprocess"},
        "permissions": [],
        "harnessVersion": {"min": "1.1.0", "max": "1.x"},
    }
    (plugin / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (plugin / "plugin.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    return plugin


class SysPluginsTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_inprocess_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            sys_root = Path(temporary) / "sys"
            empty_builtin = Path(temporary) / "builtin"
            empty_builtin.mkdir()
            plugin_dir = _write_sys_plugin(sys_root)
            import agent.config as agent_config

            with patch.object(storage, "SYS_PLUGINS_ROOT", sys_root), patch.object(
                agent_config, "PLUGIN_BUILTIN_ROOT", empty_builtin
            ):
                found = storage.discover_builtin_plugins()
                self.assertIn(PLUGIN_ID, found)
                item = found[PLUGIN_ID]
                self.assertEqual(item.manifest.runtime.kind, "inprocess")
                self.assertEqual(item.path, plugin_dir)

                target = load_file_target(item.path, "plugin:handle")
                transport = InProcessTransport(
                    plugin_id=PLUGIN_ID,
                    version=item.version,
                    target=target,
                )
                await transport.open()
                try:
                    describe = await transport.request(
                        protocol.METHOD_DESCRIBE, {}, timeout=5
                    )
                    self.assertEqual(
                        describe["capabilities"]["tool"][0]["name"], "echo"
                    )
                    result = await transport.request(
                        protocol.METHOD_TOOL_CALL,
                        {"name": "echo", "arguments": {"text": "ok"}},
                        timeout=5,
                    )
                    self.assertEqual(result["content"], "sys:ok")
                finally:
                    await transport.close()

    async def test_lifecycle_starts_builtin(self):
        with tempfile.TemporaryDirectory() as temporary:
            sys_root = Path(temporary) / "sys"
            plugin_dir = _write_sys_plugin(sys_root)
            item = InstalledPlugin(
                plugin_id=PLUGIN_ID,
                version="1.0.0",
                path=plugin_dir,
                source="builtin",
                manifest=load_manifest(
                    plugin_dir / "manifest.json", source="builtin"
                ),
            )
            registry = CapabilityRegistry()
            manager = PluginLifecycleManager(
                user_id="u1", store=None, registry=registry
            )
            manager.set_installed({PLUGIN_ID: item})
            manager.register_inprocess(
                PLUGIN_ID, load_file_target(plugin_dir, "plugin:handle")
            )
            try:
                snapshot = await manager.start(PLUGIN_ID)
                self.assertEqual(len(snapshot.tools), 1)
                text = await manager.call_tool(
                    PLUGIN_ID, "echo", {"text": "life"}
                )
                self.assertEqual(text, "sys:life")
                await manager.mark_disabled(PLUGIN_ID)
                self.assertEqual(manager.state(PLUGIN_ID).status, "disabled")
            finally:
                await manager.shutdown()


if __name__ == "__main__":
    unittest.main()
