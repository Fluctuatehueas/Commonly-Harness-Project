"""能力注册表测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus.errors import PluginError  # noqa: E402
from plugin_bus.manifest import manifest_from_dict  # noqa: E402
from plugin_bus.registry import CapabilityRegistry, build_snapshot  # noqa: E402


def _manifest(plugin_id: str, capabilities: list[str] | None = None):
    return manifest_from_dict(
        {
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "protocolVersion": "1.0",
            "author": {"name": "tester"},
            "capabilities": capabilities or ["tool"],
            "runtime": {"kind": "subprocess", "command": "python"},
            "transport": {"kind": "stdio"},
            "permissions": [],
            "harnessVersion": {"min": "1.1.0", "max": "1.x"},
        },
        harness_version="1.1.0",
    )


def _describe(capabilities: dict):
    return {"plugin_id": "x", "protocolVersion": "1.0", "capabilities": capabilities}


class RegistryTest(unittest.TestCase):
    def test_reject_undeclared_capability(self):
        with self.assertRaises(PluginError):
            build_snapshot(_manifest("demo"), _describe({"ui": [{"slot": "a"}]}))

    def test_reject_unknown_hook(self):
        with self.assertRaises(PluginError):
            build_snapshot(
                _manifest("demo", ["hook"]),
                _describe({"hook": [{"name": "on_everything"}]}),
            )

    def test_tool_conflict_skipped(self):
        registry = CapabilityRegistry()
        snapshot = build_snapshot(
            _manifest("demo-a"),
            _describe(
                {
                    "tool": [
                        {"name": "same", "description": "a", "inputSchema": {}},
                    ]
                }
            ),
        )
        registry.register(
            "u1", snapshot, reserved_names={"plugin__demo-a__same"}
        )
        self.assertTrue(registry.conflicts())
        self.assertEqual(registry.tools_for_user("u1"), [])

    def test_prompt_keyword_filter(self):
        registry = CapabilityRegistry()
        snapshot = build_snapshot(
            _manifest("demo", ["prompt"]),
            _describe(
                {
                    "prompt": [
                        {"id": "p1", "trigger": "keyword", "keywords": ["压缩"]},
                        {"id": "p2", "trigger": "always"},
                    ]
                }
            ),
        )
        registry.register("u1", snapshot)
        hits = registry.prompts_for_user("u1", "帮我压缩上下文")
        self.assertEqual({item["id"] for item in hits}, {"p1", "p2"})
        hits = registry.prompts_for_user("u1", "无关内容")
        self.assertEqual({item["id"] for item in hits}, {"p2"})

    def test_resource_catalog(self):
        registry = CapabilityRegistry()
        snapshot = build_snapshot(
            _manifest("demo", ["resource"]),
            _describe(
                {
                    "resource": [
                        {"uri": "session://x", "name": "会话", "mimeType": "text/plain"}
                    ]
                }
            ),
        )
        registry.register("u1", snapshot)
        catalog = registry.resource_catalog("u1")
        self.assertEqual(catalog[0]["uri"], "session://x")
        self.assertEqual(catalog[0]["plugin_id"], "demo")


if __name__ == "__main__":
    unittest.main()
