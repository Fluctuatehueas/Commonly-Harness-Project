"""manifest 解析与校验测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus.errors import PluginManifestError  # noqa: E402
from plugin_bus.manifest import manifest_from_dict  # noqa: E402


def _base(**overrides):
    payload = {
        "plugin_id": "demo-plugin",
        "name": "演示插件",
        "version": "1.0.0",
        "protocolVersion": "1.0",
        "description": "测试",
        "author": {"name": "tester"},
        "capabilities": ["tool"],
        "runtime": {"kind": "subprocess", "command": "python", "args": ["-m", "demo"]},
        "transport": {"kind": "stdio"},
        "permissions": [],
        "harnessVersion": {"min": "1.1.0", "max": "1.x"},
    }
    payload.update(overrides)
    return payload


class ManifestTest(unittest.TestCase):
    def test_valid_manifest(self):
        manifest = manifest_from_dict(_base(), harness_version="1.1.0")
        self.assertEqual(manifest.plugin_id, "demo-plugin")
        self.assertEqual(manifest.runtime.kind, "subprocess")
        self.assertEqual(manifest.transport.kind, "stdio")
        self.assertTrue(manifest.harness_version.matches("1.1.0"))

    def test_unknown_permission_rejected(self):
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(permissions=["root:everything"]), harness_version="1.1.0"
            )

    def test_protocol_major_mismatch_rejected(self):
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(protocolVersion="2.0"), harness_version="1.1.0"
            )

    def test_ui_requires_slots_and_permission(self):
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(capabilities=["ui"], permissions=["ui"]), harness_version="1.1.0"
            )
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(
                    capabilities=["ui"],
                    ui={"slots": [{"slot": "chat.sidebar"}]},
                    permissions=[],
                ),
                harness_version="1.1.0",
            )

    def test_remote_requires_http_url(self):
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(runtime={"kind": "remote"}, transport={"kind": "http"}),
                harness_version="1.1.0",
            )
        manifest = manifest_from_dict(
            _base(
                runtime={"kind": "remote"},
                transport={"kind": "http", "url": "https://example.com/rpc"},
            ),
            harness_version="1.1.0",
        )
        self.assertEqual(manifest.transport.url, "https://example.com/rpc")

    def test_harness_version_out_of_range(self):
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(_base(), harness_version="2.0.0")

    def test_project_level_forces_sandbox(self):
        manifest = manifest_from_dict(
            _base(), harness_version="1.1.0", project_level=True
        )
        self.assertEqual(manifest.transport.kind, "sandbox_stdio")

    def test_inprocess_only_for_builtin(self):
        payload = _base(
            runtime={"kind": "inprocess", "entry": "plugin:handle"},
            transport={"kind": "inprocess"},
        )
        manifest = manifest_from_dict(
            payload, source="builtin", harness_version="1.1.0"
        )
        self.assertEqual(manifest.runtime.kind, "inprocess")
        self.assertEqual(manifest.runtime.entry, "plugin:handle")
        self.assertEqual(manifest.transport.kind, "inprocess")

        with self.assertRaises(PluginManifestError):
            manifest_from_dict(payload, source="user", harness_version="1.1.0")
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                _base(
                    runtime={"kind": "inprocess", "entry": ""},
                    transport={"kind": "inprocess"},
                ),
                source="builtin",
                harness_version="1.1.0",
            )
        with self.assertRaises(PluginManifestError):
            manifest_from_dict(
                payload,
                source="builtin",
                harness_version="1.1.0",
                project_level=True,
            )


if __name__ == "__main__":
    unittest.main()
