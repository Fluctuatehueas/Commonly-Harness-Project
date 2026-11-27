"""权限与 host.* 白名单测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import protocol  # noqa: E402
from plugin_bus.errors import PluginPermissionDenied  # noqa: E402
from plugin_bus.manifest import manifest_from_dict  # noqa: E402
from plugin_bus.permissions import HostBridge, ensure_host_allowed, resolve_data_path  # noqa: E402


def _manifest(permissions: list[str]):
    return manifest_from_dict(
        {
            "plugin_id": "demo",
            "name": "demo",
            "version": "1.0.0",
            "protocolVersion": "1.0",
            "author": {"name": "tester"},
            "capabilities": ["tool"],
            "runtime": {"kind": "subprocess", "command": "python"},
            "transport": {"kind": "stdio"},
            "permissions": permissions,
            "harnessVersion": {"min": "1.1.0", "max": "1.x"},
        },
        harness_version="1.1.0",
    )


class PermissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_storage_kv_roundtrip(self):
        manifest = _manifest(["storage"])
        with tempfile.TemporaryDirectory() as temporary:
            bridge = HostBridge(
                manifest=manifest,
                user_id="u1",
                data_root=Path(temporary) / "data",
                logs_root=Path(temporary) / "logs",
            )
            await bridge.handle(protocol.HOST_STORAGE_SET, {"key": "k", "value": {"a": 1}})
            self.assertEqual(
                await bridge.handle(protocol.HOST_STORAGE_GET, {"key": "k"}),
                {"a": 1},
            )
            self.assertEqual(
                await bridge.handle(protocol.HOST_STORAGE_LIST, {}), ["k"]
            )
            await bridge.handle(protocol.HOST_STORAGE_DELETE, {"key": "k"})
            self.assertIsNone(
                await bridge.handle(protocol.HOST_STORAGE_GET, {"key": "k"})
            )

    async def test_undeclared_permission_denied(self):
        manifest = _manifest([])
        with tempfile.TemporaryDirectory() as temporary:
            bridge = HostBridge(
                manifest=manifest,
                user_id="u1",
                data_root=Path(temporary),
                logs_root=Path(temporary),
            )
            with self.assertRaises(PluginPermissionDenied):
                await bridge.handle(protocol.HOST_STORAGE_GET, {"key": "k"})

    async def test_unknown_host_method_denied(self):
        manifest = _manifest(["storage"])
        with tempfile.TemporaryDirectory() as temporary:
            bridge = HostBridge(
                manifest=manifest,
                user_id="u1",
                data_root=Path(temporary),
                logs_root=Path(temporary),
            )
            with self.assertRaises(PluginPermissionDenied):
                await bridge.handle("host.internal.exec", {})

    async def test_secret_from_explicit_env(self):
        manifest = _manifest(["secret"])
        with tempfile.TemporaryDirectory() as temporary:
            bridge = HostBridge(
                manifest=manifest,
                user_id="u1",
                data_root=Path(temporary),
                logs_root=Path(temporary),
            )
            with patch.dict(
                os.environ, {"HARNESS_PLUGIN_SECRET_DEMO_TOKEN": "s3cr3t"}, clear=False
            ):
                self.assertEqual(
                    await bridge.handle(protocol.HOST_SECRET_GET, {"name": "token"}),
                    "s3cr3t",
                )

    async def test_log_and_progress(self):
        manifest = _manifest([])
        received: list[dict] = []
        with tempfile.TemporaryDirectory() as temporary:
            bridge = HostBridge(
                manifest=manifest,
                user_id="u1",
                data_root=Path(temporary) / "data",
                logs_root=Path(temporary) / "logs",
                progress=received.append,
            )
            await bridge.handle(protocol.HOST_LOG, {"level": "info", "message": "hello"})
            await bridge.handle(protocol.HOST_PROGRESS, {"progress": 50})
            self.assertTrue((Path(temporary) / "logs" / "plugin.log").is_file())
            self.assertEqual(received, [{"progress": 50}])

    def test_data_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PluginPermissionDenied):
                resolve_data_path(Path(temporary), "../escape.txt")

    def test_ensure_host_allowed(self):
        manifest = _manifest([])
        with self.assertRaises(PluginPermissionDenied):
            ensure_host_allowed(manifest, protocol.HOST_SECRET_GET)
        ensure_host_allowed(manifest, protocol.HOST_LOG)


if __name__ == "__main__":
    unittest.main()
