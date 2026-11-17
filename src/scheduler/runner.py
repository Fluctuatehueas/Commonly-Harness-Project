"""执行器：按波次推进计划，处理重试、检查点与重规划。

执行单元通过 ``executor`` 注入：生产环境是「每节点独立子图」，
测试可传假执行器，因此本模块不依赖 Agent 运行时与数据库。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from .checkpoint import (
    boundary_ratio,
    measure_dimensions,
    needs_checkpoint,
    score_checkpoint,
)
from .config import TierConfig
from .models import (
    CheckpointDecision,
    FactRecord,
    PlanItem,
    RevisionRecord,
    RunResult,
    RunState,
    SchedulePlan,
    utcnow_iso,
)
from .replanner import (
    apply_scope,
    has_untried_method,
    promote_pending,
    revise,
    revision_targets,
    switch_method,
)

logger = logging.getLogger(__name__)

Emit = Callable[[dict], None]


@dataclass
class ItemOutcome:
    status: str = "done"  # done / failed / needs_hitl / skipped
    summary: str = ""
    error: str = ""
    # 节点执行后的上下文窗口占用比例，由执行器回传，写入 d5
    context_ratio: Optional[float] = None


# executor(item, state) -> ItemOutcome | str | None
Executor = Callable[[PlanItem, RunState], Awaitable[object]]
# verifier(item, state, outcome) -> (verdict, evidence)
Verifier = Callable[[PlanItem, RunState, ItemOutcome], Awaitable[tuple[str, list[str]]]]


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
        verifier: Optional[Verifier] = None,
    ) -> None:
        self._cfg = cfg
        self._executor = executor
        self._emit = emit
        self._store = store
        self._verifier = verifier

    # ---------------- 对外入口 ----------------

    async def run(self, plan: SchedulePlan, state: RunState) -> RunResult:
        state.status = "running"
        if state.started_at is None:
            state.started_at = utcnow_iso()
        # 恢复语义：上次中断时处于 running 的条目回到 pending，attempts 保留
        for item in self._items(plan):
            if item.status == "running":
                item.status = "pending"
                item.finished_at = None
        await self._persist_state(state)
        await self._persist_plan(plan, state)
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
                    if (
                        plan.pending_phases
                        and replans < self._cfg.max_replan
                        and state.delta.d5_budget_ratio < self._cfg.wrapup_ratio
                    ):
                        plan, changed, reason = promote_pending(plan)
                        replans += 1
                        state.plan_version = plan.plan_version
                        state.replan_count = replans
                        await self._persist_plan(plan, state)
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
                            verdict, evidence = await self._verify(item, outcome, state)
                            _upsert_fact(
                                state,
                                item,
                                outcome,
                                verdict=verdict,
                                evidence=evidence or None,
                            )
                            state.summary = render_facts(state.facts)
                            self._event(
                                "item_done",
                                run_id=state.run_id,
                                item_id=item.id,
                                status="done",
                                plan_version=plan.plan_version,
                                attempts=item.attempts,
                                verdict=verdict,
                            )
                            if verdict == "failed":
                                self._event(
                                    "verification_failed",
                                    run_id=state.run_id,
                                    item_id=item.id,
                                    evidence=evidence,
                                )
                                new_plan, used, paused = await self._switch_or_bubble(
                                    plan, state, item, replans
                                )
                                if new_plan is not None:
                                    plan = new_plan
                                    replans = used
                                    state.plan_version = plan.plan_version
                                    state.replan_count = replans
                                    done, failed, skipped = self._status_lists(plan)
                                    replanned = True
                                    break
                                if not paused and self._throttle_ok(
                                    state, item.phase_id, replans, ignore_interval=True
                                ):
                                    targets = revision_targets(plan, item.id)
                                    self._snapshot(
                                        plan, state, targets, "验收未通过，重开该条目及下游"
                                    )
                                    plan, changed, reason = revise(
                                        plan,
                                        item.id,
                                        reason="验收未通过，重开该条目及下游",
                                    )
                                    replans += 1
                                    state.plan_version = plan.plan_version
                                    state.replan_count = replans
                                    self._mark_replan(state, item.phase_id)
                                    await self._persist_plan(plan, state)
                                    done, failed, skipped = self._status_lists(plan)
                                    self._event(
                                        "plan_revised",
                                        run_id=state.run_id,
                                        scope="subtree",
                                        plan_version=plan.plan_version,
                                        item_ids=changed,
                                        reason=reason,
                                        plan_items=plan_items_payload(plan),
                                    )
                                    replanned = True
                                    break
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
                            if self._multi_method(plan, item.phase_id):
                                new_plan, used, paused = await self._switch_or_bubble(
                                    plan, state, item, replans
                                )
                                if new_plan is not None:
                                    plan = new_plan
                                    replans = used
                                    state.plan_version = plan.plan_version
                                    state.replan_count = replans
                                    done, failed, skipped = self._status_lists(plan)
                                    replanned = True
                                    break

                        progressed = True
                        state.nodes_since_replan += 1
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
                        if outcome.context_ratio is not None:
                            state.delta.d5_budget_ratio = max(
                                0.0, min(1.0, outcome.context_ratio)
                            )
                        await self._persist_state(state)

                        if state.status == "paused":
                            break

                        decision = self._maybe_checkpoint(item, plan, state, journal)
                        if decision is None:
                            continue
                        checkpoints += 1
                        # d4 是一次性扰动：本轮检查点消费后清空，避免连续整盘重规划
                        state.delta.d4_new_requirements = False
                        await self._persist_checkpoint(state, item, plan, decision)
                        self._event(
                            "checkpoint",
                            run_id=state.run_id,
                            item_id=item.id,
                            score=round(decision.score, 4),
                            action=decision.action,
                            scope=decision.scope,
                            reason=decision.reason,
                            impact=decision.impact,
                            uncertainty=decision.uncertainty,
                            plan_version=plan.plan_version,
                        )

                        if decision.action == "hitl":
                            state.status = "paused"
                            break
                        if decision.action == "reorder":
                            # 轻量重排没有可靠信号：只记录，不改计划、不递增版本号
                            continue
                        if decision.action == "replan" and self._throttle_ok(
                            state, item.phase_id, replans
                        ):
                            self._mark_replan(state, item.phase_id)
                            plan, changed, reason = apply_scope(
                                plan,
                                decision.scope or "reorder",
                                item_id=item.id,
                                reason=decision.reason,
                            )
                            replans += 1
                            state.plan_version = plan.plan_version
                            state.replan_count = replans
                            await self._persist_plan(plan, state)
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
                summary=state.summary,
                done=len(done),
                failed=len(failed),
                skipped=len(skipped),
                replans=replans,
                warnings=list(state.warnings),
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
                warnings=list(state.warnings),
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
                warnings=list(state.warnings),
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
        # 不可逆条目必须在派发前拿到人工授权，未授权直接暂停，不调用执行器
        if item.irreversible and not item.approved:
            return ItemOutcome(
                status="needs_hitl",
                error="不可逆操作需人工确认后执行（scheduler_replan approve）",
            )
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

    async def _verify(
        self, item: PlanItem, outcome: ItemOutcome, state: RunState
    ) -> tuple[str, list[str]]:
        """节点完成后的验收；没有验收条件时直接 unverified，不额外调模型。"""
        if self._verifier is None or not item.acceptance or not outcome.summary:
            return "unverified", []
        try:
            verdict, evidence = await self._verifier(item, state, outcome)
        except Exception as exc:
            logger.exception("节点验收异常")
            self._warn(
                state,
                "verify_failed",
                f"{item.id}: {type(exc).__name__}: {exc}",
            )
            return "unverified", []
        if verdict not in ("passed", "failed", "waived"):
            return "unverified", []
        return verdict, [str(entry) for entry in (evidence or [])]

    @staticmethod
    def _phase(plan: SchedulePlan, phase_id: str):
        return next((phase for phase in plan.phases if phase.id == phase_id), None)

    def _multi_method(self, plan: SchedulePlan, phase_id: str) -> bool:
        phase = self._phase(plan, phase_id)
        return bool(phase and len(phase.methods) > 1)

    def _throttle_ok(
        self,
        state: RunState,
        phase_id: str,
        replans: int,
        *,
        ignore_interval: bool = False,
    ) -> bool:
        """节流：总次数、同阶段次数、最小间隔、连续无进展。"""
        reason = ""
        if replans >= self._cfg.max_replan:
            reason = "重规划总次数已达上限"
        elif state.phase_replans.get(phase_id, 0) >= self._cfg.phase_replan_limit:
            reason = f"阶段 {phase_id} 重规划次数已达上限"
        elif (
            not ignore_interval
            and state.nodes_since_replan < self._cfg.replan_min_nodes
        ):
            reason = "距上次重规划完成的节点数不足"
        elif state.replans_without_progress >= 2:
            reason = "连续重规划没有新增完成节点"
        if reason:
            self._event(
                "replan_blocked",
                run_id=state.run_id,
                phase_id=phase_id,
                reason=reason,
            )
            return False
        return True

    def _mark_replan(self, state: RunState, phase_id: str) -> None:
        state.phase_replans[phase_id] = state.phase_replans.get(phase_id, 0) + 1
        if state.nodes_since_replan == 0:
            state.replans_without_progress += 1
        else:
            state.replans_without_progress = 0
        state.nodes_since_replan = 0

    async def _switch_or_bubble(
        self, plan: SchedulePlan, state: RunState, item: PlanItem, replans: int
    ) -> tuple[Optional[SchedulePlan], int, bool]:
        """失败恢复：能换方法就换；多方法阶段方法耗尽则暂停等人工。"""
        phase_id = item.phase_id
        if has_untried_method(plan, phase_id) and replans < self._cfg.max_replan:
            updated, changed, detail, ok = switch_method(plan, phase_id)
            if ok:
                state.nodes_since_replan = 0
                await self._persist_plan(updated, state)
                self._event(
                    "method_switched",
                    run_id=state.run_id,
                    phase_id=phase_id,
                    item_ids=changed,
                    reason=detail,
                    plan_version=updated.plan_version,
                    plan_items=plan_items_payload(updated),
                )
                return updated, replans + 1, False
        if self._multi_method(plan, phase_id):
            state.status = "paused"
            self._event(
                "phase_failed",
                run_id=state.run_id,
                phase_id=phase_id,
                reason="该阶段所有方法均已失败，等待人工选择",
            )
            return None, replans, True
        return None, replans, False

    def _maybe_checkpoint(
        self,
        item: PlanItem,
        plan: SchedulePlan,
        state: RunState,
        journal: int,
    ) -> Optional[CheckpointDecision]:
        if not needs_checkpoint(item, index=journal, journal=journal, cfg=self._cfg):
            return None
        impact, uncertainty = measure_dimensions(
            item,
            self._items(plan),
            state.facts,
            max_attempts=self._cfg.max_attempts,
        )
        decision = score_checkpoint(
            tier=state.tier,
            impact=impact,
            uncertainty=uncertainty,
            user_delta=state.delta.d4_new_requirements,
            global_scope=state.delta.d4_new_requirements
            and state.delta.d3_manual_intervention,
            budget_ratio=state.delta.d5_budget_ratio,
            # 不可逆门已在 _execute_item 派发前处理，这里不再重复硬触发
            irreversible=False,
            cfg=self._cfg,
        )
        decision.impact = impact
        decision.uncertainty = uncertainty
        return decision

    # ---------------- 辅助 ----------------

    @staticmethod
    def _items(plan: SchedulePlan) -> list[PlanItem]:
        return [item for phase in plan.phases for item in phase.items]

    def _status_lists(
        self, plan: SchedulePlan
    ) -> tuple[list[str], list[str], list[str]]:
        items = self._items(plan)
        return (
            [item.id for item in items if item.status == "done"],
            [item.id for item in items if item.status == "failed"],
            [item.id for item in items if item.status == "skipped"],
        )

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

    def _warn(self, state: RunState, code: str, message: str) -> None:
        """运行期降级必须同时落状态和发事件，不能只写日志。"""
        line = f"{code}: {message}"
        if line not in state.warnings:
            state.warnings.append(line)
        self._event(
            "scheduler_error",
            run_id=state.run_id,
            code=code,
            message=message,
        )

    def _snapshot(
        self, plan: SchedulePlan, state: RunState, item_ids: list[str], reason: str
    ) -> None:
        """重开前保留旧事实与摘要；只记事实级快照，不复制沙箱文件。"""
        if not item_ids:
            return
        by_id = {item.id: item for item in self._items(plan)}
        facts = {fact.item_id: fact for fact in state.facts}
        for item_id in item_ids:
            item = by_id.get(item_id)
            if item is None:
                continue
            state.revisions.append(
                RevisionRecord(
                    item_id=item.id,
                    plan_version=plan.plan_version,
                    reason=reason,
                    previous_status=item.status,
                    previous_summary=item.summary,
                    previous_finished_at=item.finished_at,
                    snapshot=facts.get(item.id),
                )
            )
        if len(state.revisions) > REVISION_LIMIT:
            del state.revisions[:-REVISION_LIMIT]
        self._event(
            "revision_snapshot",
            run_id=state.run_id,
            item_ids=item_ids,
            plan_version=plan.plan_version,
            reason=reason,
        )

    async def _persist_state(self, state: RunState) -> None:
        if self._store is None:
            return
        try:
            await self._store.update_run(state)
        except Exception as exc:
            logger.exception("调度状态落库失败")
            self._warn(state, "persist_state", f"{type(exc).__name__}: {exc}")

    async def _persist_plan(self, plan: SchedulePlan, state: RunState) -> None:
        if self._store is None:
            return
        try:
            await self._store.save_plan(plan)
        except Exception as exc:
            logger.exception("调度计划落库失败")
            self._warn(state, "persist_plan", f"{type(exc).__name__}: {exc}")

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
        except Exception as exc:
            logger.exception("检查点落库失败")
            self._warn(state, "persist_checkpoint", f"{type(exc).__name__}: {exc}")


__all__ = ["ItemOutcome", "SchedulerRunner", "render_facts"]

FACT_LIMIT = 200
REVISION_LIMIT = 100
SUMMARY_LIMIT = 3000


def render_facts(facts: list[FactRecord], limit: int = SUMMARY_LIMIT) -> str:
    """把事实账本渲染成节点可读的文本视图；只保留最近 limit 字。"""
    lines: list[str] = []
    for fact in facts[-FACT_LIMIT:]:
        line = f"『{fact.item_id}』{fact.output_digest or fact.goal}"
        if fact.acceptance:
            criteria = "；".join(entry.text for entry in fact.acceptance)
            line += f"（验收：{criteria}；结论：{fact.verdict}）"
        lines.append(line)
    return "\n".join(lines)[-limit:]


def _upsert_fact(
    state: RunState,
    item: PlanItem,
    outcome: ItemOutcome,
    *,
    verdict: str = "unverified",
    evidence: Optional[list[str]] = None,
) -> None:
    """节点完成时追加一条事实；同一条目再次完成视为重做，新记录标 revised。"""
    fact = FactRecord(
        item_id=item.id,
        phase_id=item.phase_id,
        goal=item.title,
        output_ref=f"{state.run_id}:{item.id}",
        output_digest=(outcome.summary or "").strip()[:500],
        acceptance=list(item.acceptance),
        verdict=verdict if verdict in ("passed", "failed", "waived") else "unverified",
        evidence=list(evidence) if evidence is not None else list(item.writes),
        attempts=item.attempts,
        duration_ms=_duration_ms(item),
        finished_at=item.finished_at or utcnow_iso(),
    )
    if any(existing.item_id == item.id for existing in state.facts):
        fact.revised = True
    state.facts.append(fact)
    if len(state.facts) > FACT_LIMIT:
        del state.facts[:-FACT_LIMIT]


def _duration_ms(item: PlanItem) -> int:
    if not item.started_at or not item.finished_at:
        return 0
    try:
        start = datetime.fromisoformat(item.started_at)
        end = datetime.fromisoformat(item.finished_at)
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))
