"""执行器：按波次推进计划，处理重试、检查点与重规划。

执行单元通过 ``executor`` 注入：生产环境是「每节点独立子图」，
测试可传假执行器，因此本模块不依赖 Agent 运行时与数据库。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .checkpoint import boundary_ratio, needs_checkpoint, score_checkpoint
from .config import TierConfig
from .models import (
    CheckpointDecision,
    PlanItem,
    RunResult,
    RunState,
    SchedulePlan,
    utcnow_iso,
)
from .replanner import apply_scope, promote_pending

logger = logging.getLogger(__name__)

Emit = Callable[[dict], None]


@dataclass
class ItemOutcome:
    status: str = "done"  # done / failed / needs_hitl / skipped
    summary: str = ""
    error: str = ""


# executor(item, state) -> ItemOutcome | str | None
Executor = Callable[[PlanItem, RunState], Awaitable[object]]


def plan_items_payload(plan: SchedulePlan) -> list[dict[str, object]]:
    """事件里的计划快照：前端据此按序号渲染条目与状态。"""
    return [
        {
            "id": item.id,
            "title": item.title,
            "phase_id": item.phase_id,
            "phase_title": phase.title,
            "status": item.status,
            "depends_on": item.depends_on,
        }
        for phase in plan.phases
        for item in phase.items
    ]


class SchedulerRunner:
    def __init__(
        self,
        *,
        cfg: TierConfig,
        executor: Executor,
        emit: Optional[Emit] = None,
        store: Optional[object] = None,
    ) -> None:
        self._cfg = cfg
        self._executor = executor
        self._emit = emit
        self._store = store

    # ---------------- 对外入口 ----------------

    async def run(self, plan: SchedulePlan, state: RunState) -> RunResult:
        state.status = "running"
        if state.started_at is None:
            state.started_at = utcnow_iso()
        await self._persist_state(state)
        await self._persist_plan(plan)
        self._event(
            "run_started",
            run_id=state.run_id,
            tier=state.tier.value,
            plan_version=plan.plan_version,
            item_count=self._count(plan),
        )

        done: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        checkpoints = 0
        replans = 0
        journal = 0
        guard = max(1, self._count(plan) * 4 + 10)

        try:
            while guard > 0:
                guard -= 1
                items = self._items(plan)
                pending = [item for item in items if item.status == "pending"]

                if not pending:
                    if plan.pending_phases and replans < self._cfg.max_replan:
                        plan, changed, reason = promote_pending(plan)
                        replans += 1
                        state.plan_version = plan.plan_version
                        state.replan_count = replans
                        await self._persist_plan(plan)
                        self._event(
                            "plan_revised",
                            run_id=state.run_id,
                            scope="expand",
                            plan_version=plan.plan_version,
                            item_ids=changed,
                            reason=reason,
                            plan_items=plan_items_payload(plan),
                        )
                        continue
                    break

                waves = plan.waves or [[item.id for item in pending]]
                progressed = False
                replanned = False
                for wave in waves:
                    wave_items = [
                        item for item in pending if item.id in set(wave)
                    ]
                    if not wave_items:
                        continue
                    ready, blocked = self._split_ready(plan, wave_items)
                    for item in blocked:
                        item.status = "skipped"
                        item.error = "依赖条目失败或缺失，已跳过"
                        skipped.append(item.id)
                        self._event(
                            "item_skipped",
                            run_id=state.run_id,
                            item_id=item.id,
                            reason=item.error,
                        )

                    if not ready:
                        continue
                    outcomes = await self._run_wave(ready, state)
                    for item, outcome in outcomes:
                        journal += 1
                        if outcome.status == "done":
                            item.status = "done"
                            item.summary = outcome.summary
                            item.finished_at = utcnow_iso()
                            done.append(item.id)
                            state.summary = _append_done_summary(
                                state.summary, item.id, outcome.summary
                            )
                            self._event(
                                "item_done",
                                run_id=state.run_id,
                                item_id=item.id,
                                status="done",
                                plan_version=plan.plan_version,
                                attempts=item.attempts,
                            )
                        elif outcome.status == "needs_hitl":
                            item.status = "needs_hitl"
                            item.error = outcome.error or "需要人工介入"
                            state.status = "paused"
                            self._event(
                                "item_needs_hitl",
                                run_id=state.run_id,
                                item_id=item.id,
                                reason=item.error,
                            )
                        else:
                            item.status = "failed"
                            item.error = outcome.error or outcome.summary or "执行失败"
                            failed.append(item.id)
                            self._event(
                                "item_failed",
                                run_id=state.run_id,
                                item_id=item.id,
                                error=item.error,
                                attempts=item.attempts,
                            )

                        progressed = True
                        state.done_count = len(done)
                        state.failed_count = len(failed)
                        state.remaining = [
                            i.id for i in self._items(plan) if i.status == "pending"
                        ]
                        state.delta.d1_progress = boundary_ratio(
                            len(done), self._count(plan)
                        )
                        state.delta.d2_remaining = len(
                            [i for i in self._items(plan) if i.status == "pending"]
                        )
                        await self._persist_state(state)

                        if state.status == "paused":
                            break

                        decision = self._maybe_checkpoint(
                            item, plan, state, journal, len(done), len(failed)
                        )
                        if decision is None:
                            continue
                        checkpoints += 1
                        await self._persist_checkpoint(state, item, plan, decision)
                        self._event(
                            "checkpoint",
                            run_id=state.run_id,
                            item_id=item.id,
                            score=round(decision.score, 4),
                            action=decision.action,
                            scope=decision.scope,
                            reason=decision.reason,
                            plan_version=plan.plan_version,
                        )

                        if decision.action == "hitl":
                            state.status = "paused"
                            break
                        if decision.action in ("replan", "reorder") and replans < self._cfg.max_replan:
                            plan, changed, reason = apply_scope(
                                plan,
                                decision.scope or "reorder",
                                item_id=item.id,
                                reason=decision.reason,
                            )
                            replans += 1
                            state.plan_version = plan.plan_version
                            state.replan_count = replans
                            await self._persist_plan(plan)
                            self._event(
                                "plan_revised",
                                run_id=state.run_id,
                                scope=decision.scope,
                                plan_version=plan.plan_version,
                                item_ids=changed,
                                reason=reason,
                                plan_items=plan_items_payload(plan),
                            )
                            done = [i.id for i in self._items(plan) if i.status == "done"]
                            failed = [i.id for i in self._items(plan) if i.status == "failed"]
                            skipped = [i.id for i in self._items(plan) if i.status == "skipped"]
                            # 计划已被深拷贝替换，当前 wave 引用已失效，回到外层重读。
                            replanned = True
                            break

                    if state.status == "paused" or replanned:
                        break
                if state.status == "paused" or replanned:
                    if state.status == "paused":
                        break
                    continue
                if not progressed:
                    # 没有可推进的条目：剩余项要么依赖失败，要么已成环。
                    for item in self._items(plan):
                        if item.status == "pending":
                            item.status = "skipped"
                            item.error = "无可执行的依赖前驱"
                            skipped.append(item.id)
                    await self._persist_state(state)
                    break

            state.status = "done" if not failed and state.status != "paused" else state.status
            if failed and state.status != "paused":
                state.status = "failed"
            state.finished_at = utcnow_iso()
            state.summary = self._summary(plan, done, failed, skipped)
            await self._persist_state(state)
            self._event(
                "run_finished",
                run_id=state.run_id,
                status=state.status,
                plan_version=plan.plan_version,
                done=len(done),
                failed=len(failed),
                skipped=len(skipped),
            )
            return RunResult(
                ok=state.status == "done",
                run_id=state.run_id,
                status=state.status,
                plan_version=plan.plan_version,
                done=done,
                failed=failed,
                skipped=skipped,
                summary=state.summary,
                checkpoints=checkpoints,
                replans=replans,
                error="" if state.status == "done" else state.summary,
                plan_items=plan_items_payload(plan),
            )
        except Exception as exc:  # 执行器自身异常不能打穿工具调用
            logger.exception("调度执行异常：%s", state.run_id)
            state.status = "failed"
            state.summary = f"调度执行异常：{exc}"
            state.finished_at = utcnow_iso()
            await self._persist_state(state)
            return RunResult(
                ok=False,
                run_id=state.run_id,
                status="failed",
                plan_version=plan.plan_version,
                done=done,
                failed=failed,
                skipped=skipped,
                summary=state.summary,
                checkpoints=checkpoints,
                replans=replans,
                error=str(exc),
            )

    # ---------------- 执行 ----------------

    async def _run_wave(
        self, items: list[PlanItem], state: RunState
    ) -> list[tuple[PlanItem, ItemOutcome]]:
        semaphore = asyncio.Semaphore(max(1, self._cfg.max_parallel))

        async def one(item: PlanItem) -> tuple[PlanItem, ItemOutcome]:
            async with semaphore:
                return item, await self._execute_item(item, state)

        return list(await asyncio.gather(*(one(item) for item in items)))

    async def _execute_item(self, item: PlanItem, state: RunState) -> ItemOutcome:
        item.status = "running"
        item.started_at = utcnow_iso()
        self._event(
            "item_started",
            run_id=state.run_id,
            item_id=item.id,
            phase=item.phase_id,
            plan_version=state.plan_version,
        )
        last_error = ""
        for attempt in range(1, self._cfg.max_attempts + 1):
            item.attempts = attempt
            try:
                result = await asyncio.wait_for(
                    self._executor(item, state), timeout=self._cfg.node_timeout_sec
                )
            except asyncio.TimeoutError:
                last_error = f"节点超时（{self._cfg.node_timeout_sec:.0f}s）"
            except Exception as exc:  # 单节点失败不终止整条运行
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                outcome = self._coerce(result)
                if outcome.status in ("done", "needs_hitl"):
                    return outcome
                last_error = outcome.error or outcome.summary or "执行失败"
            if item.irreversible:
                return ItemOutcome(
                    status="needs_hitl",
                    error=f"不可逆条目执行失败：{last_error}",
                )
        return ItemOutcome(status="failed", error=last_error)

    @staticmethod
    def _coerce(result: object) -> ItemOutcome:
        if isinstance(result, ItemOutcome):
            return result
        if result is None:
            return ItemOutcome(status="done", summary="")
        if isinstance(result, str):
            return ItemOutcome(status="done", summary=result)
        return ItemOutcome(status="done", summary=str(result))

    # ---------------- 检查点 ----------------

    def _maybe_checkpoint(
        self,
        item: PlanItem,
        plan: SchedulePlan,
        state: RunState,
        journal: int,
        done_count: int,
        failed_count: int,
    ) -> Optional[CheckpointDecision]:
        if not needs_checkpoint(item, index=journal, journal=journal, cfg=self._cfg):
            return None
        return score_checkpoint(
            tier=state.tier,
            impact=item.impact,
            uncertainty=item.uncertainty,
            user_delta=state.delta.d4_new_requirements,
            global_scope=state.delta.d4_new_requirements
            and state.delta.d3_manual_intervention,
            budget_ratio=boundary_ratio(done_count, max(1, self._count(plan))),
            irreversible=item.irreversible and item.status != "done",
            cfg=self._cfg,
        )

    # ---------------- 辅助 ----------------

    @staticmethod
    def _items(plan: SchedulePlan) -> list[PlanItem]:
        return [item for phase in plan.phases for item in phase.items]

    def _count(self, plan: SchedulePlan) -> int:
        return len(self._items(plan))

    @staticmethod
    def _split_ready(
        plan: SchedulePlan, items: list[PlanItem]
    ) -> tuple[list[PlanItem], list[PlanItem]]:
        by_id = {item.id: item for item in SchedulerRunner._items(plan)}
        ready: list[PlanItem] = []
        blocked: list[PlanItem] = []
        for item in items:
            states = [by_id[dep].status for dep in item.depends_on if dep in by_id]
            if any(state in ("failed", "skipped", "needs_hitl") for state in states):
                blocked.append(item)
            elif all(state in ("done", "skipped") for state in states):
                ready.append(item)
        return ready, blocked

    @staticmethod
    def _summary(
        plan: SchedulePlan, done: list[str], failed: list[str], skipped: list[str]
    ) -> str:
        total = SchedulerRunner._items(plan)
        return (
            f"共 {len(total)} 条：完成 {len(done)}，失败 {len(failed)}，"
            f"跳过 {len(skipped)}"
        )

    def _event(self, event: str, **payload: object) -> None:
        if self._emit is None:
            return
        try:
            self._emit({"type": "scheduler_progress", "event": event, **payload})
        except Exception:
            logger.exception("调度事件上报失败，已忽略")

    async def _persist_state(self, state: RunState) -> None:
        if self._store is None:
            return
        try:
            await self._store.update_run(state)
        except Exception:
            logger.exception("调度状态落库失败，已忽略")

    async def _persist_plan(self, plan: SchedulePlan) -> None:
        if self._store is None:
            return
        try:
            await self._store.save_plan(plan)
        except Exception:
            logger.exception("调度计划落库失败，已忽略")

    async def _persist_checkpoint(
        self,
        state: RunState,
        item: PlanItem,
        plan: SchedulePlan,
        decision: CheckpointDecision,
    ) -> None:
        if self._store is None:
            return
        try:
            await self._store.add_checkpoint(
                run_id=state.run_id,
                plan_version=plan.plan_version,
                decision=decision,
                item_id=item.id,
            )
        except Exception:
            logger.exception("检查点落库失败，已忽略")


__all__ = ["ItemOutcome", "SchedulerRunner"]


def _append_done_summary(current: str, item_id: str, summary: str) -> str:
    """累积已完成摘要（S），供后续节点作为上下文；只保留最近 3000 字。"""
    if not summary:
        return current
    line = f"『{item_id}』{summary.strip()[:500]}"
    merged = f"{current}\n{line}".strip() if current else line
    return merged[-3000:]
