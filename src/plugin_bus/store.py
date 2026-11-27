"""插件状态持久化（PostgreSQL 四张表）。

磁盘是包内容与版本的权威源，本模块只负责安装记录、用户启停、配置与审计。
连接惰性建立，复用 ``agent.config.POSTGRES_URI``，与 task_flow / scheduler 一致。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS plugin_install (
        user_id        TEXT NOT NULL,
        plugin_id      TEXT NOT NULL,
        version        TEXT NOT NULL,
        source         TEXT NOT NULL,
        runtime_kind   TEXT NOT NULL,
        transport_kind TEXT NOT NULL,
        endpoint       TEXT,
        sha256         TEXT,
        status         TEXT NOT NULL,
        installed_at   TIMESTAMPTZ DEFAULT now(),
        updated_at     TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (user_id, plugin_id, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plugin_install_user ON plugin_install(user_id, plugin_id, status)",
    """
    CREATE TABLE IF NOT EXISTS plugin_user_state (
        user_id        TEXT NOT NULL,
        plugin_id      TEXT NOT NULL,
        enabled        BOOLEAN NOT NULL DEFAULT true,
        current_version TEXT,
        auto_disabled  BOOLEAN NOT NULL DEFAULT false,
        updated_at     TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (user_id, plugin_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_config (
        user_id        TEXT NOT NULL,
        plugin_id      TEXT NOT NULL,
        config_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
        secret_ref     TEXT,
        updated_at     TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (user_id, plugin_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_audit (
        id         BIGSERIAL PRIMARY KEY,
        user_id    TEXT NOT NULL,
        plugin_id  TEXT NOT NULL,
        version    TEXT,
        action     TEXT NOT NULL,
        result     TEXT NOT NULL,
        detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plugin_audit_user ON plugin_audit(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_plugin_audit_plugin ON plugin_audit(user_id, plugin_id, created_at DESC)",
]


class PluginStore:
    """插件四表读写；连接惰性建立，进程级复用一个实例。"""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        self._conn: Any = None
        self._ready = False

    async def _get_conn(self) -> Any:
        if self._conn is None or self._conn.closed:
            if self._dsn is None:
                from agent.config import POSTGRES_URI

                self._dsn = POSTGRES_URI
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            self._conn = await AsyncConnection.connect(
                self._dsn,
                autocommit=True,
                prepare_threshold=None,
                row_factory=dict_row,
            )
        return self._conn

    async def setup(self) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            for statement in DDL_STATEMENTS:
                await cursor.execute(statement)
        self._ready = True
        logger.info("plugin 四表结构就绪")

    async def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            await self._conn.close()
        self._conn = None
        self._ready = False

    # ---------- 安装记录 ----------

    async def upsert_install(
        self,
        *,
        user_id: str,
        plugin_id: str,
        version: str,
        source: str,
        runtime_kind: str,
        transport_kind: str,
        endpoint: str = "",
        sha256: str = "",
        status: str = "installed",
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO plugin_install
                    (user_id, plugin_id, version, source, runtime_kind,
                     transport_kind, endpoint, sha256, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, plugin_id, version) DO UPDATE SET
                    source = EXCLUDED.source,
                    runtime_kind = EXCLUDED.runtime_kind,
                    transport_kind = EXCLUDED.transport_kind,
                    endpoint = EXCLUDED.endpoint,
                    sha256 = EXCLUDED.sha256,
                    status = EXCLUDED.status,
                    updated_at = now()
                """,
                (
                    user_id,
                    plugin_id,
                    version,
                    source,
                    runtime_kind,
                    transport_kind,
                    endpoint,
                    sha256,
                    status,
                ),
            )

    async def set_install_status(
        self, user_id: str, plugin_id: str, version: str, status: str
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE plugin_install SET status = %s, updated_at = now()
                WHERE user_id = %s AND plugin_id = %s AND version = %s
                """,
                (status, user_id, plugin_id, version),
            )

    async def get_install(
        self, user_id: str, plugin_id: str, version: str
    ) -> dict[str, Any] | None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT * FROM plugin_install
                WHERE user_id = %s AND plugin_id = %s AND version = %s
                """,
                (user_id, plugin_id, version),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_installs(self, user_id: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT * FROM plugin_install
                WHERE user_id = %s
                ORDER BY plugin_id, installed_at DESC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_installs(self, user_id: str, plugin_id: str) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM plugin_install WHERE user_id = %s AND plugin_id = %s",
                (user_id, plugin_id),
            )

    # ---------- 用户启停 ----------

    async def upsert_user_state(
        self,
        *,
        user_id: str,
        plugin_id: str,
        enabled: bool,
        current_version: str = "",
        auto_disabled: bool = False,
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO plugin_user_state
                    (user_id, plugin_id, enabled, current_version, auto_disabled)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, plugin_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    current_version = EXCLUDED.current_version,
                    auto_disabled = EXCLUDED.auto_disabled,
                    updated_at = now()
                """,
                (user_id, plugin_id, enabled, current_version, auto_disabled),
            )

    async def get_user_state(
        self, user_id: str, plugin_id: str
    ) -> dict[str, Any] | None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM plugin_user_state WHERE user_id = %s AND plugin_id = %s",
                (user_id, plugin_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_user_states(self, user_id: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM plugin_user_state WHERE user_id = %s",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_user_state(self, user_id: str, plugin_id: str) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM plugin_user_state WHERE user_id = %s AND plugin_id = %s",
                (user_id, plugin_id),
            )

    # ---------- 配置 ----------

    async def save_config(
        self,
        *,
        user_id: str,
        plugin_id: str,
        config: dict[str, Any],
        secret_ref: str = "",
    ) -> None:
        from psycopg.types.json import Jsonb

        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO plugin_config
                    (user_id, plugin_id, config_json, secret_ref)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, plugin_id) DO UPDATE SET
                    config_json = EXCLUDED.config_json,
                    secret_ref = EXCLUDED.secret_ref,
                    updated_at = now()
                """,
                (user_id, plugin_id, Jsonb(config), secret_ref),
            )

    async def get_config(self, user_id: str, plugin_id: str) -> dict[str, Any]:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM plugin_config WHERE user_id = %s AND plugin_id = %s",
                (user_id, plugin_id),
            )
            row = await cursor.fetchone()
        if not row:
            return {}
        payload = dict(row)
        config = payload.get("config_json") or {}
        return config if isinstance(config, dict) else {}

    async def delete_config(self, user_id: str, plugin_id: str) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM plugin_config WHERE user_id = %s AND plugin_id = %s",
                (user_id, plugin_id),
            )

    # ---------- 审计 ----------

    async def audit(
        self,
        *,
        user_id: str,
        plugin_id: str,
        action: str,
        result: str = "ok",
        version: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        try:
            conn = await self._get_conn()
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO plugin_audit
                        (user_id, plugin_id, version, action, result, detail)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        plugin_id,
                        version or None,
                        action,
                        result,
                        Jsonb(detail or {}),
                    ),
                )
        except Exception:
            # 审计失败不能影响主流程。
            logger.warning("写入插件审计失败: %s %s", plugin_id, action, exc_info=True)

    async def list_audit(
        self, user_id: str, *, plugin_id: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cursor:
            if plugin_id:
                await cursor.execute(
                    """
                    SELECT * FROM plugin_audit
                    WHERE user_id = %s AND plugin_id = %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (user_id, plugin_id, limit),
                )
            else:
                await cursor.execute(
                    """
                    SELECT * FROM plugin_audit
                    WHERE user_id = %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (user_id, limit),
                )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


__all__ = ["PluginStore", "DDL_STATEMENTS"]
