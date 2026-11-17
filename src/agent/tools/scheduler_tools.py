"""调度工具：scheduler_plan / scheduler_replan。

只有 ``HARNESS_SCHEDULER_EXEC=1`` 时才会注册到主 Agent；工具被显式调用后才进入
调度流程，未调用时系统行为与 P0 完全一致。

- scheduler_plan：分档 → 建计划 → （开关允许时）提交后台执行并立即返回 run_id；
- scheduler_replan：对已有运行做 reorder / subtree / full 重规划并在后台继续推进；
- 后台执行通过 ``scheduler_events`` 推送进度，终态摘要写回会话。

每个条目的执行单元是独立子图（thread_id = run_id:item_id），子图禁用调度工具，
避免递归调度。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Literal, Optional

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from scheduler.config import get_config
from scheduler.events import scheduler_events
from scheduler.features import extract_features
from scheduler.models import (
    AcceptanceItem,
    Delta,
    FactRecord,
    PlanItem,
    PlanPhaseInput,
    RunState,
    SchedulePlan,
    Tier,
    utcnow_iso,
)
from scheduler.planner import PlanError, build_plan, build_reference_plan, plan_warnings
from scheduler.replanner import apply_scope
from scheduler.runner import (
    ItemOutcome,
    SchedulerRunner,
    plan_items_payload,
    render_facts,
)
from scheduler.store import SchedulerStore
from scheduler.tiering import decide_tier

logger = logging.getLogger(__name__)

MODEL_SUMMARY_MAX_CHARS = 1200

# 后台调度只存在于当前进程；工具返回后仍由 asyncio.Task 推进。
# 同一 run 不允许重复启动，同一会话同一时间只保留一个后台调度，
# 避免模型重复调用工具时并发争抢同一个沙箱与模型配额。
_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}
_ACTIVE_BY_THREAD: dict[str, str] = {}
# 用户显式终止的 run：CancelledError 分支据此区分「用户终止」与「服务停止」。
_USER_CANCELLED: set[str] = set()


def cancel_scheduler_run(run_id: str) -> bool:
    """取消进程内正在执行的后台调度；任务不存在或已结束时返回 False。"""
    task = _ACTIVE_TASKS.get(run_id)
    if task is None or task.done():
        return False
    _USER_CANCELLED.add(run_id)
    task.cancel()
    return True


def _task_alive(run_id: str) -> bool:
    task = _ACTIVE_TASKS.get(run_id)
    return task is not None and not task.done()


def _release_run(run_id: str, thread_id: str) -> None:
    _ACTIVE_TASKS.pop(run_id, None)
    if _ACTIVE_BY_THREAD.get(thread_id) == run_id:
        _ACTIVE_BY_THREAD.pop(thread_id, None)


def _thread_conflict(thread_id: str, run_id: str) -> str | None:
    active = _ACTIVE_BY_THREAD.get(thread_id)
    if active and active != run_id and _task_alive(active):
        return active
    return None


class SchedulerPlanInput(BaseModel):
    """生成并（可选）执行调度计划。"""

    goal: str = Field(description="本轮总目标")
    phases: list[PlanPhaseInput] = Field(
        default_factory=list,
        description="两级任务层次表：阶段 P0..PN，每个阶段下是条目。使用任务流时可留空",
    )
    tier: Literal["auto", "EZ", "MD", "HD"] = Field(
        default="auto", description="复杂度档位；auto 由分档函数判定"
    )
    mode: Literal["auto", "dynamic", "reference"] = Field(
        default="auto",
        description="auto：给了 flow_id 走 reference，否则 dynamic",
    )
    flow_id: str = Field(
        default="", description="已发布的任务流 ID；提供时按任务流结构生成计划"
    )
    execute: Optional[bool] = Field(
        default=None,
        description="是否立即执行；默认跟随 HARNESS_SCHEDULER_EXEC 开关",
    )


class SchedulerReplanInput(BaseModel):
    """对已有调度运行做重规划。"""

    run_id: str = Field(description="scheduler_plan 返回的 run_id")
    scope: Literal["reorder", "subtree", "full"] = Field(
        default="reorder", description="重规划范围"
    )
    item_id: str = Field(default="", description="subtree 重规划的起点条目")
    reason: str = Field(default="", description="为什么要重规划")
    new_phases: list[PlanPhaseInput] = Field(
        default_factory=list, description="full 重规划时提交的新阶段表；留空则保留原结构重置"
    )
    approve: list[str] = Field(
        default_factory=list, description="用户确认执行的不可逆条目 ID"
    )
    new_requirement: str = Field(
        default="", description="用户本轮新增或变更的要求；非空时按全局新需求处理"
    )
    execute: Optional[bool] = Field(default=None, description="重规划后是否继续执行")


# ============================================================
# 工具工厂
# ============================================================


def create_scheduler_tools(
    *,
    config: dict[str, Any],
    store: Any,
    checkpointer: Any,
    sandbox_backend: Any,
    integration_server_id: Optional[str] = None,
    agent_profile: Optional[dict[str, Any]] = None,
) -> list[Any]:
    """构建调度工具；仅在开关打开时由 main_agent 调用。"""
    cfg = get_config()
    user_id = str(config.get("configurable", {}).get("user_id", "anonymous"))
    # 调度自己的四张表走独立 Store；注入的 store 是 Agent 长期记忆库，两者不能混用。
    # Store 按每次工具调用创建：后台 run 可以跨会话并发，不能共享同一条异步连接。

    def emit(runtime: ToolRuntime, payload: dict[str, Any]) -> None:
        # 先写进程内事件总线：后台任务在本轮 SSE 关闭后仍能推送进度。
        run_id = str(payload.get("run_id") or "")
        if run_id:
            scheduler_events.publish(run_id, payload)
        writer = getattr(runtime, "stream_writer", None)
        if writer is None:
            return
        try:
            writer({"type": "scheduler_progress", **payload})
        except Exception:  # noqa: BLE001 - 工具返回后写不进本轮 SSE 是预期情况
            pass

    def make_executor(runtime: ToolRuntime):
        """每节点独立子图；子图禁用调度工具，防止递归。"""

        async def execute(item: PlanItem, state: RunState) -> ItemOutcome:
            from agent.main_agent import create_main_agent

            child_config = {
                **config,
                "configurable": {
                    **config.get("configurable", {}),
                    "thread_id": f"{state.run_id}:{item.id}",
                },
            }
            graph = await create_main_agent(
                child_config,
                store=store,
                checkpointer=checkpointer,
                sandbox_backend=sandbox_backend,
                integration_server_id=integration_server_id,
                agent_profile=agent_profile,
                scheduler_enabled=False,
            )
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=_node_prompt(item, state))]},
                child_config,
            )
            ratio = _context_ratio(result)
            if "__interrupt__" in result:
                return ItemOutcome(
                    status="needs_hitl",
                    error="节点请求人工审批；请在主对话中确认后调用 scheduler_replan",
                    context_ratio=ratio,
                )
            return ItemOutcome(
                status="done", summary=_last_text(result), context_ratio=ratio
            )

        return execute

    def make_verifier():
        """节点验收：先跑机检；机检不能定案时再按验收条件调一次模型。"""

        async def verify(item: PlanItem, state: RunState, outcome: ItemOutcome):
            from langchain_core.messages import HumanMessage, SystemMessage

            from agent.config import get_main_model

            checks = await _run_checks(
                item.acceptance,
                sandbox_backend,
                _sandbox_workspace_root(state.thread_id),
            )
            decided = _decide_by_checks(item.acceptance, checks)
            check_notes = [note for _, note in checks]
            if decided is not None:
                return decided, check_notes

            response = await get_main_model().ainvoke(
                [
                    SystemMessage(content=_verifier_system_prompt()),
                    HumanMessage(
                        content=_verification_prompt(item, outcome, check_notes)
                    ),
                ]
            )
            verdict, evidence = _parse_verdict(getattr(response, "content", "") or "")
            return verdict, check_notes + evidence

        return verify

    async def _background_main(
        runtime: ToolRuntime,
        plan: SchedulePlan,
        state: RunState,
        store: SchedulerStore,
    ) -> None:
        runner = SchedulerRunner(
            cfg=cfg,
            executor=make_executor(runtime),
            emit=lambda payload: emit(runtime, payload),
            store=store,
            verifier=make_verifier(),
        )
        result = None
        try:
            result = await runner.run(plan, state)
            logger.info(
                "调度后台已结束：%s status=%s items=%s",
                plan.run_id,
                result.status,
                _item_count(plan),
            )
            # 执行结束后立即释放会话占用；结果回写失败不应阻塞新的调度。
            _release_run(plan.run_id, state.thread_id)
            await _record_sample(store, plan, result, state)
            await _deliver_result(runtime, plan, state, result)
        except asyncio.CancelledError:
            _release_run(plan.run_id, state.thread_id)
            cancelled_by_user = plan.run_id in _USER_CANCELLED
            _USER_CANCELLED.discard(plan.run_id)
            state.status = "cancelled" if cancelled_by_user else "interrupted"
            state.summary = (
                "后台调度已由用户终止"
                if cancelled_by_user
                else "后台调度已中断（服务停止或任务被取消）"
            )
            state.finished_at = utcnow_iso()
            if cancelled_by_user:
                # 用户终止时先抵消一次取消，允许把终态写进数据库；写完再继续抛出。
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
                try:
                    await _persist(store, state, plan)
                except asyncio.CancelledError:
                    logger.warning("取消状态落库被中断：%s", plan.run_id)
                except Exception:
                    logger.warning("取消状态落库失败：%s", plan.run_id, exc_info=True)
            emit(
                runtime,
                {
                    "event": "run_finished",
                    "run_id": plan.run_id,
                    "status": state.status,
                    "summary": state.summary,
                    "warnings": list(state.warnings),
                },
            )
            raise
        except Exception as exc:
            _release_run(plan.run_id, state.thread_id)
            logger.exception("后台调度异常：%s", plan.run_id)
            state.status = "failed"
            state.summary = f"调度执行异常：{exc}"
            state.finished_at = utcnow_iso()
            await _persist(store, state, plan)
            emit(
                runtime,
                {
                    "event": "run_finished",
                    "run_id": plan.run_id,
                    "status": "failed",
                    "summary": state.summary,
                    "warnings": list(state.warnings),
                },
            )
            await _deliver_result(runtime, plan, state, None)
        finally:
            _USER_CANCELLED.discard(plan.run_id)
            _release_run(plan.run_id, state.thread_id)
            await store.close()

    def _start_background(
        runtime: ToolRuntime,
        plan: SchedulePlan,
        state: RunState,
        store: SchedulerStore,
    ) -> dict[str, Any] | None:
        """启动后台执行；成功返回 None，冲突时返回给模型看的错误信息。"""
        conflict = _thread_conflict(state.thread_id, plan.run_id)
        if conflict:
            return {
                "ok": False,
                "error": f"该会话已有后台调度在运行（{conflict}）",
                "active_run_id": conflict,
            }
        if _task_alive(plan.run_id):
            return {
                "ok": False,
                "error": "该调度已在后台执行，请勿重复启动",
                "active_run_id": plan.run_id,
            }
        state.status = "running"
        task = asyncio.create_task(
            _background_main(runtime, plan, state, store),
            name=f"scheduler:{plan.run_id}",
        )
        _ACTIVE_TASKS[plan.run_id] = task
        if state.thread_id:
            _ACTIVE_BY_THREAD[state.thread_id] = plan.run_id
        task.add_done_callback(lambda _: _release_run(plan.run_id, state.thread_id))
        logger.info(
            "调度后台已启动：%s thread=%s items=%s",
            plan.run_id,
            state.thread_id,
            _item_count(plan),
        )
        return None

    async def _deliver_result(
        runtime: ToolRuntime,
        plan: SchedulePlan,
        state: RunState,
        result: Any,
    ) -> None:
        """把终态摘要幂等写回会话；失败只告警，不影响 run 的终态。"""
        if not state.thread_id:
            return
        try:
            from api.agent_loader import agent_loader

            published = await agent_loader.publish_scheduler_result(
                plan.run_id,
                user_id=state.user_id,
                thread_id=state.thread_id,
                content=_result_message(plan, state, result),
                timeout_sec=300.0,
            )
            if not published:
                logger.warning("调度结果未能写回会话：%s", plan.run_id)
        except Exception:
            logger.exception("调度结果回写会话失败：%s", plan.run_id)

    async def _reject_background_start(
        runtime: ToolRuntime,
        plan: SchedulePlan,
        state: RunState,
        conflict: dict[str, Any],
        store: SchedulerStore,
    ) -> None:
        """新建的 run 无法启动时落成失败态，避免前端卡片一直转圈。"""
        if str(conflict.get("active_run_id") or "") == plan.run_id:
            # 冲突就是同一个 run，它正在正常执行，不能误标失败。
            return
        state.status = "failed"
        state.summary = str(conflict.get("error") or "后台调度启动失败")
        state.finished_at = utcnow_iso()
        await _persist(store, state, plan)
        emit(
            runtime,
            {
                "event": "run_finished",
                "run_id": plan.run_id,
                "status": "failed",
                "summary": state.summary,
                "warnings": list(state.warnings),
            },
        )

    @tool(args_schema=SchedulerPlanInput)
    async def scheduler_plan(
        goal: str,
        phases: list[PlanPhaseInput],
        tier: Literal["auto", "EZ", "MD", "HD"] = "auto",
        mode: Literal["auto", "dynamic", "reference"] = "auto",
        flow_id: str = "",
        execute: Optional[bool] = None,
        runtime: ToolRuntime = None,  # type: ignore[assignment]
    ) -> Command | str:
        """生成两级任务计划，并在开关允许时提交后台执行。

        工具会立即返回 run_id，不等待计划执行完成；进度由调度事件流推送。
        适用于多步骤、有依赖或需要分阶段的复杂任务。简单任务不要调用。
        """
        cfg_now = get_config()
        chosen = _resolve_tier(goal, tier, cfg_now)
        if chosen is Tier.EZ:
            return _payload(
                runtime,
                {
                    "ok": False,
                    "tier": "EZ",
                    "guidance": "EZ 档无需调度，直接用主 Agent ReAct 循环完成",
                },
            )

        resolved_mode = mode
        if mode == "auto":
            resolved_mode = "reference" if flow_id else "dynamic"

        try:
            plan = await _build(
                goal=goal,
                tier=chosen,
                mode=resolved_mode,
                phases=phases,
                flow_id=flow_id,
                user_id=user_id,
                cfg=cfg_now,
            )
        except PlanError as exc:
            return _payload(runtime, {"ok": False, "error": "计划不合法", "issues": exc.errors})

        state = _new_state(plan, goal=goal, user_id=user_id, config=config)
        should_execute = cfg_now.exec_enabled if execute is None else bool(execute)
        if should_execute and not cfg_now.exec_enabled:
            should_execute = False
        # 每次工具调用独享一条连接；后台 run 需要持有它直到执行结束。
        store = SchedulerStore()
        handed_off = False
        try:
            persisted = await _persist(store, state, plan)
            if not persisted:
                emit(
                    runtime,
                    {
                        "event": "scheduler_error",
                        "run_id": state.run_id,
                        "code": "persist_run",
                        "message": "调度运行落库失败，仅内存执行",
                    },
                )
            emit(
                runtime,
                {
                    "event": "planned",
                    "run_id": state.run_id,
                    "goal": plan.goal,
                    "tier": plan.tier.value,
                    "mode": plan.mode,
                    "plan_version": plan.plan_version,
                    "item_count": _item_count(plan),
                    "items": _item_count(plan),
                    "waves": len(plan.waves),
                    "plan_items": plan_items_payload(plan),
                    "background": should_execute,
                },
            )

            if should_execute:
                conflict = _start_background(runtime, plan, state, store)
                if conflict is not None:
                    await _reject_background_start(
                        runtime, plan, state, conflict, store
                    )
                    return _payload(
                        runtime,
                        {
                            "run_id": plan.run_id,
                            "background": True,
                            **conflict,
                            "guidance": "等待现有后台调度结束后再发起；需要调整现有运行可调用 scheduler_replan。",
                        },
                    )
                handed_off = True
                return _started(
                    runtime,
                    plan,
                    state,
                    warnings=plan_warnings(plan, cfg_now),
                )

            await _record_sample(store, plan, None, state)
            return _finish(
                runtime,
                plan=plan,
                state=state,
                result=None,
                executed=False,
                warnings=plan_warnings(plan, cfg_now),
            )
        finally:
            if not handed_off:
                await store.close()

    @tool(args_schema=SchedulerReplanInput)
    async def scheduler_replan(
        run_id: str,
        scope: Literal["reorder", "subtree", "full"] = "reorder",
        item_id: str = "",
        reason: str = "",
        new_phases: list[PlanPhaseInput] = None,
        approve: list[str] = None,
        new_requirement: str = "",
        execute: Optional[bool] = None,
        runtime: ToolRuntime = None,  # type: ignore[assignment]
    ) -> Command | str:
        """对已有调度运行重规划：只重排 / 重置子树 / 完整重规划。

        subtree 必须提供 item_id；full 可提交新的阶段表。
        approve 用于确认执行暂停在不可逆门上的条目。
        """
        cfg_now = get_config()
        if _task_alive(run_id):
            return _payload(
                runtime,
                {
                    "ok": False,
                    "run_id": run_id,
                    "error": "该调度正在后台执行，完成后才能重规划",
                },
            )
        # 每次工具调用独享一条连接；后台 run 需要持有它直到执行结束。
        store = SchedulerStore()
        handed_off = False
        try:
            loaded = await _load(runtime, run_id, store)
            if loaded is None:
                return _payload(runtime, {"ok": False, "error": f"运行 {run_id} 不存在"})
            state, plan = loaded

            if scope == "subtree" and not item_id:
                return _payload(runtime, {"ok": False, "error": "subtree 必须提供 item_id"})

            # 人工介入与不可逆授权：approve 只解冻执行，不等于重做
            approved_ids = set(approve or [])
            if approved_ids:
                for item in _items(plan):
                    if item.id in approved_ids:
                        item.approved = True
                        if item.status == "needs_hitl":
                            item.status = "pending"
                            item.error = ""
            state.delta.d3_manual_intervention = True
            if new_requirement.strip():
                state.delta.d4_new_requirements = True

            if scope == "full" and new_phases:
                try:
                    rebuilt = build_plan(
                        run_id=run_id,
                        goal=state.goal,
                        tier=state.tier,
                        phases=new_phases,
                        cfg=cfg_now,
                    )
                except PlanError as exc:
                    return _payload(
                        runtime,
                        {"ok": False, "error": "新计划不合法", "issues": exc.errors},
                    )
                _carry_done(plan, rebuilt, state.facts)
                plan = rebuilt
                changed = [item.id for item in _items(plan)]
                detail = reason or "按新阶段表完整重规划"
            else:
                plan, changed, detail = apply_scope(
                    plan, scope, item_id=item_id or None, reason=reason
                )

            state.plan_version = plan.plan_version
            if changed:
                state.replan_count += 1
            state.status = "running"
            state.remaining = [i.id for i in _items(plan) if i.status == "pending"]
            should_execute = cfg_now.exec_enabled if execute is None else bool(execute)
            if should_execute and not cfg_now.exec_enabled:
                should_execute = False
            persisted = await _persist(store, state, plan)
            if not persisted:
                emit(
                    runtime,
                    {
                        "event": "scheduler_error",
                        "run_id": state.run_id,
                        "code": "persist_run",
                        "message": "调度运行落库失败，仅内存执行",
                    },
                )
            emit(
                runtime,
                {
                    "event": "plan_revised",
                    "run_id": run_id,
                    "scope": scope,
                    "plan_version": plan.plan_version,
                    "item_ids": changed,
                    "reason": detail,
                    "plan_items": plan_items_payload(plan),
                    "background": should_execute,
                },
            )

            if should_execute:
                conflict = _start_background(runtime, plan, state, store)
                if conflict is not None:
                    await _reject_background_start(
                        runtime, plan, state, conflict, store
                    )
                    return _payload(
                        runtime,
                        {
                            "run_id": run_id,
                            "background": True,
                            **conflict,
                            "guidance": "等待现有后台调度结束后再重试。",
                        },
                    )
                handed_off = True
                return _started(
                    runtime,
                    plan,
                    state,
                    warnings=plan_warnings(plan, cfg_now),
                )

            await _record_sample(store, plan, None, state)
            return _finish(
                runtime,
                plan=plan,
                state=state,
                result=None,
                executed=False,
                warnings=plan_warnings(plan, cfg_now),
            )
        finally:
            if not handed_off:
                await store.close()

    return [scheduler_plan, scheduler_replan]


# ============================================================
# 内部辅助
# ============================================================


def _resolve_tier(goal: str, tier: str, cfg) -> Tier:
    if tier != "auto":
        return Tier(tier)
    return decide_tier(extract_features(goal), cfg).tier


async def _build(
    *,
    goal: str,
    tier: Tier,
    mode: str,
    phases: list[PlanPhaseInput],
    flow_id: str,
    user_id: str,
    cfg,
) -> SchedulePlan:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    if mode == "reference" or flow_id:
        flow = await _load_flow(flow_id, user_id)
        if flow is None:
            raise PlanError([f"任务流 {flow_id or '(未指定)'} 不存在或未发布"])
        return build_reference_plan(run_id=run_id, flow=flow, cfg=cfg, tier=tier)
    if not phases:
        raise PlanError(["dynamic 模式必须提供 phases"])
    return build_plan(
        run_id=run_id,
        goal=goal,
        tier=tier,
        phases=phases,
        cfg=cfg,
        mode="dynamic",
    )


async def _load_flow(flow_id: str, user_id: str):
    if not flow_id:
        return None
    from api.task_flow import get_store
    from task_flow.models import TaskFlow

    store = await get_store()
    row = await store.get_flow(flow_id)
    if not row or row.get("status") != "published" or not row.get("schema"):
        return None
    return TaskFlow.model_validate(row["schema"])


def _new_state(
    plan: SchedulePlan, *, goal: str, user_id: str, config: dict[str, Any]
) -> RunState:
    return RunState(
        run_id=plan.run_id,
        user_id=user_id,
        thread_id=str(config.get("configurable", {}).get("thread_id", "")),
        goal=goal,
        mode=plan.mode,
        tier=plan.tier,
        plan_version=plan.plan_version,
        status="created",
        remaining=[item.id for item in _items(plan) if item.status == "pending"],
        delta=Delta(d2_remaining=_item_count(plan)),
        item_count=_item_count(plan),
    )


async def _persist(store: Any, state: RunState, plan: SchedulePlan) -> bool:
    """初始落库；失败时把原因写进 state.warnings，由调用方上报事件。"""
    if store is None:
        return False
    try:
        await store.setup()
        await store.create_run(state)
        await store.save_plan(plan)
        return True
    except Exception as exc:
        logger.exception("调度运行落库失败，继续内存执行")
        state.warnings.append(f"persist_run: {type(exc).__name__}: {exc}")
        return False


async def _load(
    runtime: ToolRuntime, run_id: str, store: Any
) -> Optional[tuple[RunState, SchedulePlan]]:
    """优先读数据库：后台执行会持续更新 run/plan，图里的快照只作兜底。"""
    try:
        if store is not None:
            row = await store.get_run(run_id)
            plan_row = await store.latest_plan(run_id)
            if row and plan_row:
                return (
                    RunState.model_validate(row["state"]),
                    SchedulePlan.model_validate(plan_row),
                )
    except Exception:
        logger.exception("从数据库恢复调度运行失败")

    snapshot = (getattr(runtime, "state", {}) or {}).get("scheduler") or {}
    if str(snapshot.get("run_id", "")) == run_id and snapshot.get("plan"):
        try:
            return (
                RunState.model_validate(snapshot["state"]),
                SchedulePlan.model_validate(snapshot["plan"]),
            )
        except Exception:
            logger.exception("图状态中的调度快照无法解析")
    return None


async def _record_sample(
    store: Any, plan: SchedulePlan, result: Any, state: RunState
) -> None:
    """落一条主动学习样本：特征 + 档位 + 执行结果，供后续校准与评估集扩增。"""
    if store is None or result is None:
        return
    try:
        await store.setup()
        await store.add_sample(
            run_id=plan.run_id,
            features=extract_features(plan.goal).model_dump(mode="json"),
            tier=plan.tier.value,
            outcome={
                "status": result.status,
                "item_count": _item_count(plan),
                "done": len(result.done),
                "failed": len(result.failed),
                "skipped": len(result.skipped),
                "checkpoints": result.checkpoints,
                "replans": result.replans,
            },
            label_source="auto",
            label_confidence=0.0,
        )
    except Exception as exc:
        logger.exception("调度样本落库失败")
        state.warnings.append(f"persist_sample: {type(exc).__name__}: {exc}")


def _finish(
    runtime: ToolRuntime,
    *,
    plan: SchedulePlan,
    state: RunState,
    result: Any,
    executed: bool,
    warnings: list[str],
) -> Command | str:
    if result is not None:
        state = state.model_copy(
            update={
                "status": result.status,
                "plan_version": result.plan_version,
                "summary": result.summary,
                "remaining": [i.id for i in _items(plan) if i.status == "pending"],
            }
        )
    payload = {
        "ok": True if result is None else result.ok,
        "executed": executed,
        "run_id": plan.run_id,
        "tier": plan.tier.value,
        "mode": plan.mode,
        "plan_version": plan.plan_version,
        "status": state.status,
        "phases": [
            {
                "id": phase.id,
                "title": phase.title,
                "active_method_id": phase.active_method_id,
                "tried_methods": phase.tried_methods,
                "methods": [
                    {"id": method.id, "title": method.title, "reason": method.reason}
                    for method in phase.methods
                ],
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "status": item.status,
                        "depends_on": item.depends_on,
                        "acceptance": [entry.text for entry in item.acceptance],
                    }
                    for item in phase.items
                ],
            }
            for phase in plan.phases
        ],
        "pending_phases": [phase.id for phase in plan.pending_phases],
        "waves": plan.waves,
        "conflicts": plan.conflicts,
        "warnings": warnings,
        "summary": state.summary,
        "facts": [
            {
                "item_id": fact.item_id,
                "verdict": fact.verdict,
                "output_ref": fact.output_ref,
                "revised": fact.revised,
            }
            for fact in state.facts
        ],
        "done": list(result.done) if result else [],
        "failed": list(result.failed) if result else [],
        "skipped": list(result.skipped) if result else [],
        "checkpoints": result.checkpoints if result else 0,
        "replans": result.replans if result else 0,
        "warnings": list(state.warnings),
        "revisions": [
            {
                "item_id": revision.item_id,
                "plan_version": revision.plan_version,
                "reason": revision.reason,
                "previous_status": revision.previous_status,
                "previous_summary": revision.previous_summary[:200],
            }
            for revision in state.revisions
        ],
        "guidance": ""
        if executed
        else "计划已生成但未执行：HARNESS_SCHEDULER_EXEC 未开启或 execute=false。"
        "开启开关后重新调用即可按波次执行，也可以直接按计划自行推进。",
    }
    return _payload(
        runtime,
        payload,
        scheduler={
            "run_id": plan.run_id,
            "state": state.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
    )


def _started(
    runtime: ToolRuntime,
    plan: SchedulePlan,
    state: RunState,
    *,
    warnings: list[str],
) -> Command | str:
    """后台执行已提交时立即返回，避免主对话等待整个计划跑完。"""
    payload = {
        "ok": True,
        "executed": True,
        "background": True,
        "run_id": plan.run_id,
        "tier": plan.tier.value,
        "mode": plan.mode,
        "plan_version": plan.plan_version,
        "status": "running",
        "item_count": _item_count(plan),
        "waves": plan.waves,
        "warnings": warnings,
        "plan_items": plan_items_payload(plan),
        "guidance": (
            "后台调度已启动：不要重复调用 scheduler_plan；进度由调度事件流推送，"
            "完成后结果会写入会话。需要调整时调用 scheduler_replan。"
        ),
    }
    return _payload(
        runtime,
        payload,
        scheduler={
            "run_id": plan.run_id,
            "state": state.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
    )


def _payload(
    runtime: ToolRuntime, payload: dict[str, Any], scheduler: Optional[dict[str, Any]] = None
) -> Command | str:
    text = json.dumps(payload, ensure_ascii=False)
    tool_call_id = getattr(runtime, "tool_call_id", None) if runtime is not None else None
    if not tool_call_id or scheduler is None:
        return text
    return Command(
        update={
            "scheduler": scheduler,
            "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
        }
    )


def _items(plan: SchedulePlan) -> list[PlanItem]:
    return [item for phase in plan.phases for item in phase.items]


def _item_count(plan: SchedulePlan) -> int:
    return len(_items(plan))


def _carry_done(
    old: SchedulePlan, new: SchedulePlan, facts: list[FactRecord]
) -> None:
    """完整重规划时用事实账本恢复已完成条目；没有事实的旧运行回退到旧计划状态。"""
    by_item = {fact.item_id: fact for fact in facts}
    legacy = {
        item.id: item
        for item in _items(old)
        if item.status in ("done", "skipped")
    }
    for item in _items(new):
        fact = by_item.get(item.id)
        if fact is not None:
            item.status = "done"
            item.summary = fact.output_digest
            item.finished_at = fact.finished_at
            continue
        previous = legacy.get(item.id)
        if previous is not None:
            item.status = previous.status  # type: ItemStatus
            item.summary = previous.summary
            item.finished_at = previous.finished_at


def _result_message(plan: SchedulePlan, state: RunState, result: Any) -> str:
    """后台调度终态摘要：写进会话的消息必须能独立看懂。"""
    if result is None:
        status = state.status
        summary = state.summary
        done = len([i for i in _items(plan) if i.status == "done"])
        failed = len([i for i in _items(plan) if i.status == "failed"])
        skipped = len([i for i in _items(plan) if i.status == "skipped"])
        failed_ids: list[str] = []
        skipped_ids: list[str] = []
    else:
        status = result.status
        summary = result.summary
        done = len(result.done)
        failed = len(result.failed)
        skipped = len(result.skipped)
        failed_ids = list(result.failed)
        skipped_ids = list(result.skipped)

    lines = [
        f"后台调度已结束（{plan.run_id}）",
        f"状态：{status}；共 {_item_count(plan)} 条，完成 {done}，失败 {failed}，跳过 {skipped}",
    ]
    if summary:
        lines.append(f"摘要：{summary}")
    if failed_ids:
        lines.append("失败条目：" + "、".join(failed_ids))
    if skipped_ids:
        lines.append("跳过条目：" + "、".join(skipped_ids))
    if state.warnings:
        lines.append("警告：" + "；".join(str(item) for item in state.warnings[-5:]))
    if status == "paused":
        lines.append("有节点需要人工确认；确认后可用 scheduler_replan 继续执行。")
    return "\n".join(lines)


def _node_prompt(item: PlanItem, state: RunState) -> str:
    root = _sandbox_workspace_root(state.thread_id)
    project_kind = "free"
    try:
        from agent.project_context import current_project_optional

        project = current_project_optional()
        if project is not None and project.kind == "project":
            project_kind = "project"
    except Exception:
        pass
    if project_kind == "project":
        workspace = (
            f"【工作区】项目会话：文件工具中的 `/` 是项目根；"
            f"命令在沙箱 `{root}` 执行。不要使用 `/workspace/...` 绝对路径写文件。"
        )
    else:
        workspace = (
            f"【工作区】自由会话：文件工具中的 `/` 映射到 `{root}`。"
            "写入请使用虚拟路径（如 `/data/nums.csv`），不要使用 `/workspace/...` 绝对路径；"
            "任务说明中的 `/workspace/data/x` 必须改写为虚拟路径 `/data/x`。"
            "命令在会话目录执行，shell 中直接使用相对路径（如 `python script.py`）。"
            "示例：`/workspace/data/nums.csv` → 文件工具写 `/data/nums.csv`，"
            "shell 命令写 `data/nums.csv`。"
            "检查产出时使用文件工具的虚拟路径，不要用 shell 直接看 `/workspace` 根目录。"
        )
    lines = [
        "你在执行一个已调度任务节点。不要重新规划，不要调用 scheduler_plan。",
        workspace,
        f"总目标：{state.goal}",
        f"阶段：{item.phase_id}",
        f"条目：{item.title}",
        f"说明：{item.instruction}",
    ]
    if item.acceptance:
        lines.append(f"完成标准：{item.acceptance}")
    if state.facts:
        lines.append(f"已完成事实（S）：\n{render_facts(state.facts)}")
    elif state.summary:
        lines.append(f"已完成摘要：\n{state.summary[-3000:]}")
    if item.writes:
        lines.append(f"允许写入：{', '.join(item.writes)}")
    lines.append("只完成该条目，不要展开其他条目；完成后用 1-3 句话总结产出。")
    return "\n".join(lines)


def _verifier_system_prompt() -> str:
    """验收角色提示词；优先读 verification_expert 的 YAML，读不到时用内置兜底。"""
    try:
        from agent.subagents.configs import config_path
        from agent.subagents.loader import read_subagent_config

        config = read_subagent_config(config_path("verification_expert.yaml"))
        return str(config["system_prompt"])
    except Exception:
        logger.warning("读取 verification_expert 配置失败，使用内置提示词", exc_info=True)
        return "你是验收专家。只按给定验收条件判断产出是否通过，输出严格 JSON，不重做任务。"


def _dig(data: Any, key: str) -> Any:
    """按 a.b.0 形式取值，取不到返回 None。"""
    current = data
    for part in key.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _sandbox_workspace_root(thread_id: str) -> str:
    """返回调度节点/机检应使用的沙箱工作目录。

    自由会话使用 ``/workspace/sessions/{thread_id}``；项目会话使用
    ``/workspace/{project_id}``。节点和验收必须使用同一个根，否则会出现
    「文件写到了但机检看不到」或「右侧文件面板看不到」。
    """
    try:
        from agent.project_context import (
            current_project_optional,
            sandbox_project_dir,
            sandbox_session_dir,
        )

        project = current_project_optional()
        if project is not None and project.kind == "project":
            return sandbox_project_dir(project.id)
        return sandbox_session_dir(thread_id)
    except Exception:
        logger.warning("解析调度工作区失败，回退到会话目录", exc_info=True)
        return f"/workspace/sessions/{thread_id}"


def _workspace_target(target: str, root: str) -> str:
    """把模型给出的路径映射到工作区根，兼容 /workspace、虚拟路径和相对路径。"""
    text = str(target or "").strip()
    if not text or not root:
        return text
    if text == root or text.startswith(f"{root}/"):
        return text
    if text.startswith("/workspace/sessions/"):
        # 已经是显式会话目录，不再二次拼接。
        return text
    if text.startswith("/workspace/"):
        return f"{root}{text[len('/workspace'):]}"
    if text.startswith("/"):
        return f"{root}{text}"
    return f"{root}/{text}"


async def _run_one_check(
    check: Any, backend: Any, workspace_root: str = ""
) -> tuple[Optional[bool], str]:
    """执行一条机检；返回 (通过与否, 证据文案)。"""
    try:
        if check.kind == "command_exit0":
            command = str(check.target or "")
            if workspace_root and command.strip():
                # 模型常用 /workspace/... 绝对路径；自由会话需映射到会话目录。
                if (
                    workspace_root.startswith("/workspace/sessions/")
                    and "/workspace/sessions/" not in command
                ):
                    command = command.replace("/workspace/", f"{workspace_root}/")
                command = f"cd '{workspace_root}' && {command}"
            response = await asyncio.to_thread(backend.execute, command, timeout=60)
            code = int(getattr(response, "exit_code", 1))
            return code == 0, f"command_exit0 `{check.target}` → exit={code}"

        target = _workspace_target(check.target, workspace_root)
        responses = await asyncio.to_thread(backend.download_files, [target])
        response = responses[0] if responses else None
        if (
            response is None
            or getattr(response, "error", None)
            or getattr(response, "content", None) is None
        ):
            return False, f"{check.kind} {check.target}: 文件不可读"

        if check.kind == "file_exists":
            return True, f"file_exists {check.target}: 存在"

        text = response.content.decode("utf-8", errors="replace")
        if check.kind == "regex":
            matched = re.search(check.expect, text, flags=re.M) is not None
            return matched, (
                f"regex {check.target} /{check.expect}/: "
                f"{'匹配' if matched else '未匹配'}"
            )
        if check.kind == "field_equals":
            key, _, expected = check.expect.partition("=")
            value = _dig(json.loads(text), key)
            matched = str(value) == expected.strip()
            return matched, (
                f"field_equals {check.target} {key}: "
                f"{value!r} vs {expected.strip()!r}"
            )
        return False, f"未知机检类型 {check.kind}"
    except Exception as exc:
        return False, f"{check.kind} {check.target}: {type(exc).__name__}: {exc}"


async def _run_checks(
    acceptance: list[AcceptanceItem], backend: Any, workspace_root: str = ""
) -> list[tuple[Optional[bool], str]]:
    """对所有带 check 的条件执行机检；None 表示无法机检。"""
    results: list[tuple[Optional[bool], str]] = []
    for entry in acceptance:
        check = entry.check
        if check is None:
            continue
        if backend is None:
            results.append((None, f"{check.kind} {check.target}: 无可用沙箱"))
            continue
        results.append(await _run_one_check(check, backend, workspace_root))
    return results


def _decide_by_checks(
    acceptance: list[AcceptanceItem], results: list[tuple[Optional[bool], str]]
) -> Optional[str]:
    """机检能直接定案时返回 passed/failed，否则返回 None 交模型判断。"""
    if any(ok is False for ok, _ in results):
        return "failed"
    if (
        acceptance
        and all(entry.check is not None for entry in acceptance)
        and len(results) == len(acceptance)
        and all(ok is True for ok, _ in results)
    ):
        return "passed"
    return None


def _verification_prompt(
    item: PlanItem, outcome: ItemOutcome, check_notes: list[str]
) -> str:
    criteria = "\n".join(f"- {entry.text}" for entry in item.acceptance)
    checks = "\n".join(f"- {note}" for note in check_notes) or "（无可机检规则）"
    return (
        f"计划条目：{item.title}\n"
        f"执行说明：{item.instruction}\n"
        f"验收条件：\n{criteria}\n"
        f"机检结果：\n{checks}\n"
        f"产出摘要：\n{(outcome.summary or '')[:2000]}\n\n"
        "逐条判断验收条件是否满足；机检未覆盖的条件才需要你判断，不要重做任务。\n"
        '只输出 JSON：{"verdict": "passed" 或 "failed", "reason": "一句话", '
        '"evidence": ["可核对的事实"]}'
    )


def _parse_verdict(text: str) -> tuple[str, list[str]]:
    """解析验收输出；解析不了时退回 unverified，绝不猜 passed。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(cleaned)
    except Exception:
        if "不通过" in cleaned or '"failed"' in cleaned:
            return "failed", []
        return "unverified", []
    verdict = str(data.get("verdict", "")).strip().lower()
    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [evidence]
    return verdict, [str(entry) for entry in evidence]


def _context_ratio(result: dict[str, Any]) -> Optional[float]:
    """节点执行后的上下文占用比例：近似 token / 窗口；取不到时返回 None。"""
    try:
        from langchain_core.messages.utils import count_tokens_approximately

        from agent.context.budget import build_budget

        messages = result.get("messages") or []
        used = int(count_tokens_approximately(messages=list(messages)))
        window = max(int(build_budget().window), 1)
        return max(0.0, min(1.0, used / window))
    except Exception:
        logger.warning("上下文占用估算失败，d5 保持原值", exc_info=True)
        return None


def _last_text(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None:
            continue
        text = content if isinstance(content, str) else str(content)
        if text.strip():
            return text.strip()[:MODEL_SUMMARY_MAX_CHARS]
    return ""


__all__ = [
    "SchedulerPlanInput",
    "SchedulerReplanInput",
    "cancel_scheduler_run",
    "create_scheduler_tools",
]
