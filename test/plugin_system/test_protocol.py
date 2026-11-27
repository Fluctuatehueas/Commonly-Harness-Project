"""Plugin Protocol 信封与方法测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def test_request_envelope(self):
        message = protocol.request(protocol.METHOD_TOOL_CALL, {"name": "x"}, request_id="1")
        self.assertEqual(message["jsonrpc"], "2.0")
        self.assertEqual(message["id"], "1")
        self.assertEqual(message["method"], "tool.call")
        self.assertEqual(protocol.message_kind(message), "request")

    def test_notification_has_no_id(self):
        message = protocol.notification(protocol.HOST_LOG, {"message": "hi"})
        self.assertNotIn("id", message)
        self.assertEqual(protocol.message_kind(message), "notification")

    def test_response_and_error(self):
        ok = protocol.success_response("1", {"ok": True})
        self.assertEqual(protocol.message_kind(ok), "response")
        failed = protocol.error_response("1", protocol.ERROR_NOT_READY, "no")
        self.assertEqual(failed["error"]["code"], protocol.ERROR_NOT_READY)

    def test_parse_and_dumps_roundtrip(self):
        message = {"jsonrpc": "2.0", "id": "a", "result": {"x": 1}}
        self.assertEqual(protocol.parse_message(protocol.dumps(message)), message)

    def test_protocol_compatibility(self):
        self.assertTrue(protocol.protocol_compatible("1.0"))
        self.assertFalse(protocol.protocol_compatible("2.0"))
        self.assertFalse(protocol.protocol_compatible("1.9"))
        self.assertFalse(protocol.protocol_compatible("bad"))

    def test_method_sets(self):
        self.assertIn(protocol.METHOD_INITIALIZE, protocol.BUS_METHODS)
        self.assertIn(protocol.HOST_STORAGE_GET, protocol.HOST_METHODS)
        self.assertNotIn("host.internal", protocol.HOST_METHODS)


if __name__ == "__main__":
    unittest.main()
