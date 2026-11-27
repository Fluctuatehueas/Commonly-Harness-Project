"""PostgreSQL 四表集成测试。

数据库不可达时整组跳过，不影响其它单测；可达时建表、跑 CRUD 并清理测试数据。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.config import POSTGRES_URI  # noqa: E402
from plugin_bus.store import PluginStore  # noqa: E402

USER_ID = "__plugin_test_store__"
PLUGIN_ID = "test-plugin"
TABLES = ("plugin_install", "plugin_user_state", "plugin_config", "plugin_audit")

try:
    import psycopg

    with psycopg.connect(POSTGRES_URI, connect_timeout=2):
        pass
    DB_AVAILABLE = True
    DB_ERROR = ""
except Exception as error:  # noqa: BLE001 - 数据库不可达时只跳过本组测试
    DB_AVAILABLE = False
    DB_ERROR = f"{type(error).__name__}: {error}"


async def _cleanup(store: PluginStore) -> None:
    conn = await store._get_conn()
    async with conn.cursor() as cursor:
        for table in TABLES:
            await cursor.execute(
                f"DELETE FROM {table} WHERE user_id = %s", (USER_ID,)
            )


@unittest.skipUnless(DB_AVAILABLE, f"PostgreSQL 不可达: {DB_ERROR}")
class StoreIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = PluginStore()
        await self.store.setup()
        await _cleanup(self.store)

    async def asyncTearDown(self) -> None:
        await _cleanup(self.store)
        await self.store.close()

    async def test_tables_created(self) -> None:
        conn = await self.store._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name LIKE 'plugin\\_%'
                """
            )
            found = {row["table_name"] for row in await cursor.fetchall()}
        self.assertTrue(set(TABLES) <= found, found)

    async def test_install_state_config_audit_roundtrip(self) -> None:
        await self.store.upsert_install(
            user_id=USER_ID,
            plugin_id=PLUGIN_ID,
            version="1.0.0",
            source="user",
            runtime_kind="subprocess",
            transport_kind="stdio",
            sha256="a" * 64,
            status="installed",
        )
        await self.store.upsert_install(
            user_id=USER_ID,
            plugin_id=PLUGIN_ID,
            version="1.0.0",
            source="user",
            runtime_kind="subprocess",
            transport_kind="stdio",
            sha256="a" * 64,
            status="verified",
        )
        installs = await self.store.list_installs(USER_ID)
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0]["status"], "verified")

        await self.store.set_install_status(USER_ID, PLUGIN_ID, "1.0.0", "running")
        row = await self.store.get_install(USER_ID, PLUGIN_ID, "1.0.0")
        self.assertEqual(row["status"], "running")

        await self.store.upsert_user_state(
            user_id=USER_ID,
            plugin_id=PLUGIN_ID,
            enabled=True,
            current_version="1.0.0",
        )
        state = await self.store.get_user_state(USER_ID, PLUGIN_ID)
        self.assertTrue(state["enabled"])

        await self.store.save_config(
            user_id=USER_ID,
            plugin_id=PLUGIN_ID,
            config={"nested": {"a": 1}},
            secret_ref="${SECRET:test}",
        )
        config = await self.store.get_config(USER_ID, PLUGIN_ID)
        self.assertEqual(config["nested"]["a"], 1)

        await self.store.audit(
            user_id=USER_ID,
            plugin_id=PLUGIN_ID,
            version="1.0.0",
            action="install",
            result="ok",
            detail={"source": "test"},
        )
        audits = await self.store.list_audit(USER_ID, plugin_id=PLUGIN_ID)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["detail"]["source"], "test")

        await self.store.delete_installs(USER_ID, PLUGIN_ID)
        await self.store.delete_user_state(USER_ID, PLUGIN_ID)
        await self.store.delete_config(USER_ID, PLUGIN_ID)
        self.assertEqual(await self.store.list_installs(USER_ID), [])
        self.assertIsNone(await self.store.get_user_state(USER_ID, PLUGIN_ID))
        self.assertEqual(await self.store.get_config(USER_ID, PLUGIN_ID), {})


if __name__ == "__main__":
    unittest.main()
