"""调度运行持久化：run / plan / checkpoint / sample 四张表。

连接方式与 ``task_flow/store.py`` 一致：psycopg3 异步 + dict_row，惰性建连。
纯算法模块不依赖本文件；Runner 通过可选 store 注入，测试可传内存实现。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .models import CheckpointDecision, RunState, SchedulePlan

logger = logging.getLogger(__name__)

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS scheduler_run (
        run_id        TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL,
        thread_id     TEXT NOT NULL DEFAULT '',
        mode          TEXT NOT NULL,
        tier          TEXT NOT NULL,
        status        TEXT NOT NULL,
        goal          TEXT NOT NULL DEFAULT '',
        plan_version  INT NOT NULL DEFAULT 1,
        state         JSONB NOT NULL,
        created_at    TIMESTAMPTZ DEFAULT now(),
        updated_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scheduler_run_user ON scheduler_run(user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scheduler_run_thread ON scheduler_run(thread_id, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS scheduler_plan (
        run_id        TEXT NOT NULL,
        version       INT NOT NULL,
        plan          JSONB NOT NULL,
        created_at    TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (run_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduler_checkpoint (
        id            BIGSERIAL PRIMARY KEY,
        run_id        TEXT NOT NULL,
        item_id       TEXT,
        plan_version  INT NOT NULL,
        score         REAL NOT NULL,
        scope         TEXT,
        action        TEXT NOT NULL DEFAULT 'continue',
        decision      JSONB NOT NULL,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scheduler_checkpoint_run ON scheduler_checkpoint(run_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS scheduler_sample (
        id            BIGSERIAL PRIMARY KEY,
        run_id        TEXT NOT NULL,
        features      JSONB NOT NULL,
        tier_chosen   TEXT NOT NULL,
        outcome       JSONB NOT NULL,
        label_source  TEXT,
        label_confidence REAL,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
]


class SchedulerStore:
    """调度四表的读写。连接惰性建立，进程级复用。"""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        self._conn: Any = None
        self._ready = False

    async def _get_conn(self):
        if self._conn is None or self._conn.closed:
            if self._dsn is None:
                from agent.config import DB_CONNECT_TIMEOUT, POSTGRES_URI

                self._dsn = POSTGRES_URI
            else:
                from agent.config import DB_CONNECT_TIMEOUT
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            # DSN 已写 connect_timeout 时以 DSN 为准；否则显式传入，
            # 避免数据库不可达时后台任务无限等待建连。
            kwargs: dict[str, Any] = {}
            if "connect_timeout" not in str(self._dsn):
                kwargs["connect_timeout"] = DB_CONNECT_TIMEOUT
            self._conn = await AsyncConnection.connect(
                self._dsn,
                autocommit=True,
                prepare_threshold=None,
                row_factory=dict_row,
                **kwargs,
            )
        return self._conn

    async def setup(self) -> None:
        if self._ready:
            return
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            for statement in DDL_STATEMENTS:
                await cur.execute(statement)
        self._ready = True
        logger.info("scheduler 表结构就绪")

    async def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            await self._conn.close()
        self._conn = None
        self._ready = False

    async def mark_cancelled(
        self, run_id: str, summary: str = "后台调度已由用户终止"
    ) -> None:
        """把没有进程内任务的运行（created/paused）直接落成取消终态。"""
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE scheduler_run
                SET status = 'cancelled',
                    state = jsonb_set(
                        jsonb_set(
                            jsonb_set(state, '{status}', '"cancelled"'::jsonb, true),
                            '{summary}', to_jsonb(%s::text), true
                        ),
                        '{finished_at}', to_jsonb(now()::text), true
                    ),
                    updated_at = now()
                WHERE run_id = %s
                """,
                (summary, run_id),
            )

    async def mark_runs_interrupted(self) -> int:
        """启动时把上一进程遗留的 created/running 运行标记为中断。

        后台任务只存在于进程内；进程重启后它们不会再被推进，
        必须显式落成终态，避免前端一直等待。
        """
        await self.setup()
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE scheduler_run
                SET status = 'interrupted',
                    state = jsonb_set(state, '{status}', '"interrupted"'::jsonb, true),
                    updated_at = now()
                WHERE status IN ('created', 'running')
                """
            )
            return int(cur.rowcount or 0)

    async def create_run(self, state: RunState) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scheduler_run
                    (run_id, user_id, thread_id, mode, tier, status, goal, plan_version, state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    tier = EXCLUDED.tier,
                    plan_version = EXCLUDED.plan_version,
                    state = EXCLUDED.state,
                    updated_at = now()
                """,
                (
                    state.run_id,
                    state.user_id,
                    state.thread_id,
                    state.mode,
                    state.tier.value,
                    state.status,
                    state.goal,
                    state.plan_version,
                    json.dumps(state.model_dump(mode="json"), ensure_ascii=False),
                ),
            )

    async def update_run(self, state: RunState) -> None:
        await self.create_run(state)

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM scheduler_run WHERE run_id = %s", (run_id,))
            return await cur.fetchone()

    async def list_runs(
        self, user_id: str, thread_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """按会话列出最近的调度运行，供前端后台任务栏展示。"""
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT run_id, mode, tier, status, goal, plan_version, state,
                       created_at, updated_at
                FROM scheduler_run
                WHERE user_id = %s AND thread_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, thread_id, max(1, min(int(limit), 100))),
            )
            return list(await cur.fetchall() or [])

    async def save_plan(self, plan: SchedulePlan) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scheduler_plan (run_id, version, plan)
                VALUES (%s, %s, %s)
                ON CONFLICT (run_id, version) DO UPDATE SET plan = EXCLUDED.plan
                """,
                (
                    plan.run_id,
                    plan.plan_version,
                    json.dumps(plan.model_dump(mode="json"), ensure_ascii=False),
                ),
            )

    async def latest_plan(self, run_id: str) -> Optional[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT plan FROM scheduler_plan WHERE run_id = %s ORDER BY version DESC LIMIT 1",
                (run_id,),
            )
            row = await cur.fetchone()
            return (row or {}).get("plan")

    async def add_checkpoint(
        self,
        *,
        run_id: str,
        plan_version: int,
        decision: CheckpointDecision,
        item_id: Optional[str] = None,
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scheduler_checkpoint
                    (run_id, item_id, plan_version, score, scope, action, decision)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    item_id,
                    plan_version,
                    decision.score,
                    decision.scope,
                    decision.action,
                    json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
                ),
            )

    async def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM scheduler_checkpoint WHERE run_id = %s ORDER BY id",
                (run_id,),
            )
            return list(await cur.fetchall() or [])

    async def add_sample(
        self,
        *,
        run_id: str,
        features: dict[str, Any],
        tier: str,
        outcome: dict[str, Any],
        label_source: str = "auto",
        label_confidence: float = 0.0,
    ) -> None:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scheduler_sample
                    (run_id, features, tier_chosen, outcome, label_source, label_confidence)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    json.dumps(features, ensure_ascii=False),
                    tier,
                    json.dumps(outcome, ensure_ascii=False),
                    label_source,
                    label_confidence,
                ),
            )


__all__ = ["DDL_STATEMENTS", "SchedulerStore"]
