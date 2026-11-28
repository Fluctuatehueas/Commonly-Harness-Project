"""命令目录与只读执行测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from commands import registry  # noqa: E402
from commands.schema import CommandContext, CommandError  # noqa: E402
from commands.service import execute, execute_for_agent  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_catalog_contains_core_commands(self):
        names = {item["name"] for item in registry.catalog()}
        for name in ("help", "status", "delete", "agent", "mcp", "plugin", "skill"):
            self.assertIn(name, names)
        self.assertNotIn("list_agents", names)

    def test_executor_split(self):
        self.assertEqual(registry.get_spec("help").executor, "backend")
        self.assertEqual(registry.get_spec("delete").executor, "frontend")
        self.assertTrue(registry.get_spec("delete").requires_confirm)

    def test_unknown_command(self):
        with self.assertRaises(CommandError) as context:
            registry.get_spec("nope")
        self.assertEqual(context.exception.code, "COMMAND_NOT_FOUND")


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_help_returns_table(self):
        result = await execute(CommandContext(user_id="u1", name="help"))
        self.assertEqual(result.kind, "table")
        self.assertTrue(result.rows)
        self.assertIn("/help", result.rows[0][0])

    async def test_frontend_command_rejected_by_backend(self):
        with self.assertRaises(CommandError) as context:
            await execute(CommandContext(user_id="u1", name="delete"))
        self.assertEqual(context.exception.code, "COMMAND_EXECUTOR_MISMATCH")

    async def test_agent_can_only_run_readonly_commands(self):
        text = await execute_for_agent("u1", "", "/help status")
        self.assertIn("/status", text)
        with self.assertRaises(CommandError) as context:
            await execute_for_agent("u1", "", "/delete -f")
        self.assertEqual(context.exception.code, "COMMAND_NOT_ALLOWED")

    async def test_agent_unknown_command(self):
        with self.assertRaises(CommandError):
            await execute_for_agent("u1", "", "/nope")


if __name__ == "__main__":
    unittest.main()
