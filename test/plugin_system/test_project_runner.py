"""项目级插件：发现、强制沙箱与一次性调用链路。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol  # noqa: E402
from plugin_bus.project_runner import ProjectPluginRunner  # noqa: E402


class FakeTransport:
    def __init__(self, installed, backend, remote_root):
        self.installed = installed
        self.backend = backend
        self.remote_root = remote_root
        self.requests: list[tuple[str, dict]] = []
        self.opened = False

    async def open(self):
        self.opened = True

    async def request(self, method, params, *, timeout):
        self.requests.append((method, params))
        if method == protocol.METHOD_DESCRIBE:
            return {
                "plugin_id": "demo",
                "protocolVersion": "1.0",
                "capabilities": {
                    "tool": [{"name": "echo", "inputSchema": {}}]
                },
            }
        if method == protocol.METHOD_TOOL_CALL:
            return {"content": f"project:{params['name']}"}
        return {"ok": True}

    async def notify(self, method, params):
        return None

    async def close(self):
        return None


def _write_project_plugin(project_root: Path) -> None:
    plugin = project_root / ".commonharness" / "plugins" / "demo"
    plugin.mkdir(parents=True)
    manifest = {
        "plugin_id": "demo",
        "name": "项目插件",
        "version": "1.0.0",
        "protocolVersion": "1.0",
        "author": {"name": "tester"},
        "capabilities": ["tool"],
        "runtime": {"kind": "subprocess", "command": "python"},
        "transport": {"kind": "stdio"},
        "permissions": [],
        "harnessVersion": {"min": "1.1.0", "max": "1.x"},
    }
    (plugin / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (plugin / "main.py").write_text("print('demo')", encoding="utf-8")


class ProjectRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_discover_and_call_in_sandbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_project_plugin(root)
            transports: list[FakeTransport] = []

            def factory(installed, backend, remote_root):
                transport = FakeTransport(installed, backend, remote_root)
                transports.append(transport)
                return transport

            async def sandbox_provider(user_id: str):
                return object()

            runner = ProjectPluginRunner(
                user_id="u1",
                project_root=root,
                sandbox_provider=sandbox_provider,
                transport_factory=factory,
            )
            plugins = runner.list_plugins()
            self.assertEqual([item.plugin_id for item in plugins], ["demo"])
            self.assertEqual(plugins[0].manifest.transport.kind, "sandbox_stdio")

            describe = await runner.describe("demo")
            self.assertEqual(
                describe["capabilities"]["tool"][0]["name"], "echo"
            )
            result = await runner.call_tool("demo", "echo", {"value": 1})
            self.assertEqual(result, "project:echo")

            self.assertEqual(len(transports), 1)
            transport = transports[0]
            self.assertTrue(transport.opened)
            init_params = transport.requests[0][1]
            self.assertEqual(init_params["project_root"], str(root.resolve()))
            self.assertIn("/tmp/harness-project-plugins/u1/", transport.remote_root)
            await runner.close()

    async def test_missing_plugin_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            async def sandbox_provider(user_id: str):
                return object()

            runner = ProjectPluginRunner(
                user_id="u1",
                project_root=Path(temporary),
                sandbox_provider=sandbox_provider,
                transport_factory=lambda *args: FakeTransport(*args),
            )
            with self.assertRaises(Exception):
                await runner.call_tool("missing", "echo", {})


if __name__ == "__main__":
    unittest.main()
