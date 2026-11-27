"""运行时 + 真实数据库 + 磁盘自愈集成测试。

验证：磁盘有插件包、PG 没有记录时，启动运行时能补登记；数据库不可达时跳过。
"""

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

from agent.config import POSTGRES_URI  # noqa: E402
from plugin_bus.runtime import PluginRuntime  # noqa: E402
from plugin_bus.store import PluginStore  # noqa: E402

USER_ID = "__plugin_runtime_test__"
PLUGIN_ID = "demo-plugin"
TABLES = ("plugin_install", "plugin_user_state", "plugin_config", "plugin_audit")

try:
    import psycopg

    with psycopg.connect(POSTGRES_URI, connect_timeout=2):
        pass
    DB_AVAILABLE = True
    DB_ERROR = ""
except Exception as error:  # noqa: BLE001
    DB_AVAILABLE = False
    DB_ERROR = f"{type(error).__name__}: {error}"


def _write_plugin(user_root: Path) -> None:
    version_dir = user_root / "users" / USER_ID / PLUGIN_ID / "versions" / "1.0.0"
    version_dir.mkdir(parents=True)
    manifest = {
        "plugin_id": PLUGIN_ID,
        "name": "演示插件",
        "version": "1.0.0",
        "protocolVersion": "1.0",
        "author": {"name": "tester"},
        "capabilities": ["tool"],
        "runtime": {"kind": "subprocess", "command": "python"},
        "transport": {"kind": "stdio"},
        "permissions": [],
        "harnessVersion": {"min": "1.1.0", "max": "1.x"},
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (user_root / "users" / USER_ID / PLUGIN_ID / "current.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )


async def _cleanup() -> None:
    store = PluginStore()
    await store.setup()
    conn = await store._get_conn()
    async with conn.cursor() as cursor:
        for table in TABLES:
            await cursor.execute(
                f"DELETE FROM {table} WHERE user_id = %s", (USER_ID,)
            )
    await store.close()


def _write_sys_plugin(root: Path) -> None:
    plugin = root / "builtin-demo"
    plugin.mkdir(parents=True)
    manifest = {
        "plugin_id": "builtin-demo",
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
    (plugin / "plugin.py").write_text(
        """
def handle(method, params):
    if method == "plugin.initialize":
        return {"ok": True}
    if method == "plugin.describe":
        return {
            "plugin_id": "builtin-demo",
            "protocolVersion": "1.0",
            "capabilities": {
                "tool": [{"name": "echo", "description": "", "inputSchema": {}}]
            },
        }
    if method == "plugin.health":
        return {"ok": True}
    if method == "tool.call":
        text = (params.get("arguments") or {}).get("text", "")
        return {"content": "sys:" + str(text)}
    return {"ok": True}
""",
        encoding="utf-8",
    )


@unittest.skipUnless(DB_AVAILABLE, f"PostgreSQL 不可达: {DB_ERROR}")
class RuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_repairs_missing_install_row(self) -> None:
        import agent.config as agent_config

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_plugin(root)
            await _cleanup()
            with patch.object(agent_config, "PLUGIN_USERS_ROOT", root / "users"), patch.object(
                agent_config, "HARNESS_HOME", root
            ):
                runtime = PluginRuntime(USER_ID)
                try:
                    await runtime.initialize()
                    items = await runtime.list_installed()
                    ids = [item["plugin_id"] for item in items]
                    self.assertIn(PLUGIN_ID, ids)
                    # 系统内置插件同样可见，但当前用例只关心用户安装的自愈记录。
                    self.assertTrue(
                        all(
                            item["builtin"] or item["plugin_id"] == PLUGIN_ID
                            for item in items
                        ),
                        ids,
                    )
                    item = next(
                        entry for entry in items if entry["plugin_id"] == PLUGIN_ID
                    )
                    self.assertTrue(item["enabled"])

                    row = await runtime.store.get_install(USER_ID, PLUGIN_ID, "1.0.0")
                    self.assertIsNotNone(row)
                    self.assertEqual(row["status"], "verified")
                    audits = await runtime.store.list_audit(
                        USER_ID, plugin_id=PLUGIN_ID
                    )
                    self.assertTrue(
                        any(item["action"] == "repair" for item in audits), audits
                    )
                finally:
                    await runtime.shutdown()
            await _cleanup()

    async def test_builtin_inprocess_plugin_runs(self) -> None:
        import agent.config as agent_config
        from plugin_bus import storage

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sys_root = root / "sys"
            builtin_root = root / "builtin"
            builtin_root.mkdir()
            _write_sys_plugin(sys_root)
            await _cleanup()
            with patch.object(agent_config, "PLUGIN_USERS_ROOT", root / "users"), patch.object(
                agent_config, "HARNESS_HOME", root
            ), patch.object(
                agent_config, "PLUGIN_BUILTIN_ROOT", builtin_root
            ), patch.object(
                storage, "SYS_PLUGINS_ROOT", sys_root
            ):
                runtime = PluginRuntime(USER_ID)
                try:
                    await runtime.initialize()
                    items = await runtime.list_installed()
                    self.assertEqual(
                        [item["plugin_id"] for item in items], ["builtin-demo"]
                    )
                    self.assertTrue(items[0]["builtin"])
                    self.assertEqual(items[0]["runtime"]["kind"], "inprocess")

                    await runtime.toggle("builtin-demo", True)
                    text = await runtime.call_tool(
                        "builtin-demo", "echo", {"text": "db"}
                    )
                    self.assertEqual(text, "sys:db")
                    await runtime.toggle("builtin-demo", False)
                    self.assertFalse(
                        (await runtime.detail("builtin-demo"))["enabled"]
                    )
                finally:
                    await runtime.shutdown()
            await _cleanup()


if __name__ == "__main__":
    unittest.main()
