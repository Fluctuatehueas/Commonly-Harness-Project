"""任务流 PostgreSQL 持久化。

两张表：task_flow（schema + plan + 检索文档）、task_flow_execution（执行记录）。
连接复用 agent.config.POSTGRES_URI 自建专用连接（psycopg3 异步，dict_row 行工厂），
不依赖 LangGraph Store，避免与 checkpoint 表耦合。

embedding 列暂不建：v1 召回走关键词（见 router.py），待确认实例有 pgvector 再加。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .models import Execution, ExecutionPlan, TaskFlow

logger = logging.getLogger(__name__)

# psycopg3 的 prepared statement 不支持多语句，DDL 必须逐条执行
DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS task_flow (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL,
        name          TEXT NOT NULL,
        status        TEXT NOT NULL,
        source        TEXT NOT NULL,
        schema        JSONB NOT NULL,
        plan          JSONB,
        schema_hash   TEXT,
        search_doc    JSONB,
        created_at    TIMESTAMPTZ DEFAULT now(),
        updated_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_flow_user ON task_flow(user_id, status, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS task_flow_execution (
        id            TEXT PRIMARY KEY,
        flow_id       TEXT NOT NULL REFERENCES task_flow(id),
        user_id       TEXT NOT NULL,
        thread_id     TEXT NOT NULL DEFAULT '',
        schema_hash   TEXT NOT NULL,
        entry_step    TEXT,
        status        TEXT NOT NULL,
        step_traces   JSONB NOT NULL DEFAULT '[]',
        trace_id      TEXT,
        created_at    TIMESTAMPTZ DEFAULT now(),
        finished_at   TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_execution_flow ON task_flow_execution(flow_id, created_at DESC)",
]

BUILTIN_USER_ID = "__builtin__"


class TaskFlowStore:
    """任务流存储。连接惰性建立，进程级复用一个实例（见 api/task_flow.py）。"""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        self._conn: Any = None

    async def _get_conn(self):
        if self._conn is None or self._conn.closed:
            if self._dsn is None:
                from agent.config import POSTGRES_URI

                self._dsn = POSTGRES_URI
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            # prepare_threshold=None 禁用预编译：DDL/动态 SQL 无需 prepared statement
            self._conn = await AsyncConnection.connect(
                self._dsn, autocommit=True, prepare_threshold=None, row_factory=dict_row
            )
        return self._conn

    async def setup(self) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            for statement in DDL_STATEMENTS:
                await cur.execute(statement)
        logger.info("task_flow 表结构就绪")

    async def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            await self._conn.close()
        self._conn = None

    # ----------------------------------------------------------
    # 任务流 CRUD
    # ----------------------------------------------------------

    async def upsert_flow(
        self,
        flow: TaskFlow,
        user_id: str,
        plan: Optional[ExecutionPlan] = None,
        search_doc: Optional[dict] = None,
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO task_flow (id, user_id, name, status, source, schema, plan, schema_hash, search_doc, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, status = EXCLUDED.status, source = EXCLUDED.source,
                    schema = EXCLUDED.schema, plan = EXCLUDED.plan,
                    schema_hash = EXCLUDED.schema_hash, search_doc = EXCLUDED.search_doc,
                    updated_at = now()
                """,
                (
                    flow.id,
                    user_id,
                    flow.name,
                    flow.status,
                    flow.source,
                    json.dumps(flow.model_dump(mode="json")),
                    json.dumps(plan.model_dump(mode="json")) if plan else None,
                    plan.schema_hash if plan else None,
                    json.dumps(search_doc) if search_doc else None,
                ),
            )

    async def get_flow(self, flow_id: str) -> Optional[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM task_flow WHERE id = %s", (flow_id,))
            return await cur.fetchone()

    async def list_flows(
        self, user_id: str, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """返回（内置流 + 当前用户流），按更新时间倒序，分页。"""
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM task_flow WHERE user_id IN (%s, %s)",
                (user_id, BUILTIN_USER_ID),
            )
            total = (await cur.fetchone())["n"]
            await cur.execute(
                """
                SELECT id, user_id, name, status, source, schema_hash, search_doc, updated_at
                FROM task_flow WHERE user_id IN (%s, %s)
                ORDER BY updated_at DESC LIMIT %s OFFSET %s
                """,
                (user_id, BUILTIN_USER_ID, size, (page - 1) * size),
            )
            return await cur.fetchall(), total

    async def delete_flow(self, flow_id: str, user_id: str) -> bool:
        """删除用户自己的流；内置流与他人流返回 False。"""
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM task_flow WHERE id = %s AND user_id = %s", (flow_id, user_id)
            )
            return cur.rowcount > 0

    async def published_flows(self, user_id: str) -> list[dict[str, Any]]:
        """路由召回候选：当前用户 + 内置的已发布流。"""
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, name, search_doc, plan FROM task_flow
                WHERE status = 'published' AND user_id IN (%s, %s)
                """,
                (user_id, BUILTIN_USER_ID),
            )
            return await cur.fetchall()

    # ----------------------------------------------------------
    # 执行记录
    # ----------------------------------------------------------

    async def save_execution(self, execution: Execution) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO task_flow_execution
                    (id, flow_id, user_id, thread_id, schema_hash, entry_step, status,
                     step_traces, trace_id, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status, step_traces = EXCLUDED.step_traces,
                    trace_id = EXCLUDED.trace_id, finished_at = EXCLUDED.finished_at
                """,
                (
                    execution.id,
                    execution.flow_id,
                    execution.user_id,
                    execution.thread_id,
                    execution.schema_hash,
                    execution.entry_step,
                    execution.status,
                    json.dumps([t.model_dump(mode="json") for t in execution.step_traces]),
                    execution.trace_id,
                    execution.finished_at,
                ),
            )

    async def list_executions(
        self, flow_id: str, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM task_flow_execution WHERE flow_id = %s", (flow_id,)
            )
            total = (await cur.fetchone())["n"]
            await cur.execute(
                """
                SELECT id, flow_id, user_id, thread_id, status, entry_step, trace_id, created_at, finished_at
                FROM task_flow_execution WHERE flow_id = %s
                ORDER BY created_at DESC LIMIT %s OFFSET %s
                """,
                (flow_id, size, (page - 1) * size),
            )
            return await cur.fetchall(), total

    async def get_execution(self, execution_id: str) -> Optional[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM task_flow_execution WHERE id = %s", (execution_id,)
            )
            return await cur.fetchone()
