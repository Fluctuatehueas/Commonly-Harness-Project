"""内置插件 text-toolkit：发现、describe 与三个工具。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol, storage  # noqa: E402
from plugin_bus.transport.inprocess import InProcessTransport, load_file_target  # noqa: E402

PLUGIN_ID = "text-toolkit"


class TextToolkitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        found = storage.discover_sys_plugins()
        self.assertIn(PLUGIN_ID, found)
        self.item = found[PLUGIN_ID]
        target = load_file_target(self.item.path, "plugin:handle")
        self.transport = InProcessTransport(
            plugin_id=PLUGIN_ID, version=self.item.version, target=target
        )
        await self.transport.open()
        self.addAsyncCleanup(self.transport.close)

    async def _call(self, name: str, arguments: dict) -> dict:
        result = await self.transport.request(
            protocol.METHOD_TOOL_CALL,
            {"name": name, "arguments": arguments},
            timeout=5,
        )
        self.assertIsInstance(result, dict)
        return result

    async def test_manifest_and_tools(self):
        self.assertEqual(self.item.manifest.runtime.kind, "inprocess")
        describe = await self.transport.request(protocol.METHOD_DESCRIBE, {}, timeout=5)
        names = {
            tool["name"] for tool in describe["capabilities"]["tool"]
        }
        self.assertEqual(names, {"text_stats", "text_transform", "find_replace"})

    async def test_text_stats(self):
        result = await self._call("text_stats", {"text": "hello world\n\n第二段"})
        stats = json.loads(result["content"])
        self.assertEqual(stats["words"], 3)
        self.assertEqual(stats["paragraphs"], 2)
        self.assertGreater(stats["chars"], stats["chars_no_spaces"])

    async def test_transform_and_replace(self):
        slug = await self._call("text_transform", {"text": "Hello, 世界!", "mode": "slug"})
        self.assertEqual(slug["content"], "hello-世界")

        replaced = await self._call(
            "find_replace",
            {"text": "Foo foo FOO", "search": "foo", "replace": "bar", "count": 2},
        )
        payload = json.loads(replaced["content"])
        self.assertEqual(payload["replaced"], 2)
        self.assertEqual(payload["text"], "bar bar FOO")

    async def test_limits(self):
        with self.assertRaises(Exception):
            await self._call("text_stats", {"text": "x" * 30000})
        with self.assertRaises(Exception):
            await self._call("text_transform", {"text": "x", "mode": "unknown"})


if __name__ == "__main__":
    unittest.main()
