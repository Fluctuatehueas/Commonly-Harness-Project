"""stdio 传输端到端测试：握手、describe、tool.call 与反向 host 调用。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol  # noqa: E402
from plugin_bus.transport.stdio import StdioTransport  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_stdio_plugin.py"


class StdioTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip_with_reverse_host_call(self):
        calls: list[tuple[str, dict]] = []

        async def host_handler(method: str, params: dict):
            calls.append((method, params))
            return {"ok": True}

        with tempfile.TemporaryDirectory() as temporary:
            transport = StdioTransport(
                plugin_id="fixture",
                version="1.0.0",
                cwd=Path(temporary),
                command=sys.executable,
                args=[str(FIXTURE)],
                host_handler=host_handler,
                log_path=Path(temporary) / "stdio.log",
            )
            await transport.open()
            try:
                initialized = await transport.request(
                    protocol.METHOD_INITIALIZE,
                    {"protocolVersion": "1.0"},
                    timeout=10,
                )
                self.assertEqual(initialized, {"ok": True})
                describe = await transport.request(
                    protocol.METHOD_DESCRIBE, {}, timeout=10
                )
                self.assertEqual(describe["capabilities"]["tool"][0]["name"], "echo")
                result = await transport.request(
                    protocol.METHOD_TOOL_CALL,
                    {"name": "echo", "arguments": {}},
                    timeout=10,
                )
                self.assertEqual(result["content"], "host-ok:True")
                self.assertEqual(calls[0][0], protocol.HOST_STORAGE_SET)
                self.assertEqual(calls[0][1]["key"], "k")
            finally:
                await transport.close()

    async def test_timeout_when_plugin_silent(self):
        with tempfile.TemporaryDirectory() as temporary:
            transport = StdioTransport(
                plugin_id="silent",
                version="1.0.0",
                cwd=Path(temporary),
                command=sys.executable,
                args=["-c", "import time; time.sleep(30)"],
                host_handler=None,
            )
            await transport.open()
            try:
                with self.assertRaises(Exception):
                    await transport.request(
                        protocol.METHOD_HEALTH, {}, timeout=0.3
                    )
            finally:
                await transport.close()


if __name__ == "__main__":
    unittest.main()
