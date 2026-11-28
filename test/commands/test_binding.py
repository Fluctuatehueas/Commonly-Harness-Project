"""命令绑定：归一化、缓存哈希、入参校验。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.command_binding import (  # noqa: E402
    binding_hash,
    normalize_binding,
    validate_binding,
)
from agent.schema import ChatRequest  # noqa: E402


class BindingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        import agent.config as agent_config

        self.patches = [
            patch.object(agent_config, "HARNESS_HOME", self.root),
            patch.object(agent_config, "SKILLS_ROOT", self.root / "skills" / "builtin"),
        ]
        (self.root / "skills" / "builtin" / "main").mkdir(parents=True, exist_ok=True)
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_normalize_dedupes_and_limits(self):
        data = normalize_binding(
            {
                "skills": ["a", "a", "b"] + [f"s{index}" for index in range(10)],
                "mcp_ids": [],
                "plugin_ids": [],
            }
        )
        self.assertIsNotNone(data)
        self.assertEqual(data["skills"][:2], ["a", "b"])
        self.assertLessEqual(len(data["skills"]), 8)

    def test_empty_binding_returns_none(self):
        self.assertIsNone(normalize_binding(None))
        self.assertIsNone(normalize_binding({"skills": [], "mcp_ids": []}))
        self.assertIsNone(normalize_binding({}))

    def test_hash_stable_and_order_sensitive(self):
        first = binding_hash({"skills": ["a", "b"]})
        second = binding_hash({"skills": ["a", "b"]})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)
        self.assertNotEqual(first, binding_hash({"skills": ["b", "a"]}))

    async def test_validate_reports_missing_skill(self):
        problems = await validate_binding("u1", {"skills": ["missing-skill"]})
        self.assertTrue(problems)
        self.assertIn("missing-skill", problems[0])

    async def test_validate_accepts_existing_skill(self):
        skill_dir = (
            self.root / "skills" / "users" / "u1" / "main" / "demo-skill"
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# demo", encoding="utf-8")
        problems = await validate_binding("u1", {"skills": ["demo-skill"]})
        self.assertEqual(problems, [])

    def test_chat_request_normalizes_binding(self):
        request = ChatRequest(
            message="hi",
            binding={"skills": ["a", "a"], "mcp_ids": ["m1"], "plugin_ids": []},
        )
        self.assertEqual(request.binding.skills, ["a"])
        self.assertEqual(request.binding.mcp_ids, ["m1"])


if __name__ == "__main__":
    unittest.main()
