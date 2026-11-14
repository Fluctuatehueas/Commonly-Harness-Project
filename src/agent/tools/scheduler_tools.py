"""调度工具：scheduler_plan / scheduler_replan。

只有 ``HARNESS_SCHEDULER_EXEC=1`` 时才会注册到主 Agent；工具被显式调用后才进入
调度流程，未调用时系统行为与 P0 完全一致。

- scheduler_plan：分档 → 建计划 → （开关允许时）按波次执行；
- scheduler_replan：对已有运行做 reorder / subtree / full 重规划并继续推进。

每个条目的执行单元是独立子图（thread_id = run_id:item_id），子图禁用调度工具，
避免递归调度。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal, Optional

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from scheduler.config import get_config
from scheduler.features import extract_features
from scheduler.models import (
    Delta,
    PlanItem,
    PlanPhaseInput,
    RunState,
    SchedulePlan,
    Tier,
)
from scheduler.planner import PlanError, build_plan, build_reference_plan, plan_warnings
from scheduler.replanner import apply_scope
from scheduler.runner import ItemOutcome, SchedulerRunner, plan_items_payload
from scheduler.tiering import decide_tier

logger = logging.getLogger(__name__)

MODEL_SUMMARY_MAX_CHARS = 1200


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

    def emit(runtime: ToolRuntime, payload: dict[str, Any]) -> None:
        writer = getattr(runtime, "stream_writer", None)
        if writer is None:
            return
        try:
            writer({"type": "scheduler_progress", **payload})
        except Exception:  # noqa: BLE001 - 事件失败不影响调度
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
            if "__interrupt__" in result:
                return ItemOutcome(
                    status="needs_hitl",
                    error="节点请求人工审批；请在主对话中确认后调用 scheduler_replan",
                )
            return ItemOutcome(status="done", summary=_last_text(result))

        return execute

    async def _run(runtime: ToolRuntime, plan: SchedulePlan, state: RunState, execute: bool):
        if not execute:
            return None
        runner = SchedulerRunner(
            cfg=cfg,
            executor=make_executor(runtime),
            emit=lambda payload: emit(runtime, payload),
            store=store,
        )
        return await runner.run(plan, state)

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
        """生成两级任务计划，并在开关允许时按波次执行。

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
        await _persist(store, state, plan)
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
            },
        )

        should_execute = cfg_now.exec_enabled if execute is None else bool(execute)
        if should_execute and not cfg_now.exec_enabled:
            should_execute = False
        result = await _run(runtime, plan, state, should_execute)
        await _record_sample(store, plan, result)

        return _finish(
            runtime,
            plan=plan,
            state=state,
            result=result,
            executed=should_execute,
            warnings=plan_warnings(plan, cfg_now),
        )

    @tool(args_schema=SchedulerReplanInput)
    async def scheduler_replan(
        run_id: str,
        scope: Literal["reorder", "subtree", "full"] = "reorder",
        item_id: str = "",
        reason: str = "",
        new_phases: list[PlanPhaseInput] = None,
        execute: Optional[bool] = None,
        runtime: ToolRuntime = None,  # type: ignore[assignment]
    ) -> Command | str:
        """对已有调度运行重规划：只重排 / 重置子树 / 完整重规划。

        subtree 必须提供 item_id；full 可提交新的阶段表。
        """
        cfg_now = get_config()
        loaded = await _load(runtime, run_id)
        if loaded is None:
            return _payload(runtime, {"ok": False, "error": f"运行 {run_id} 不存在"})
        state, plan = loaded

        if scope == "subtree" and not item_id:
            return _payload(runtime, {"ok": False, "error": "subtree 必须提供 item_id"})

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
                return _payload(runtime, {"ok": False, "error": "新计划不合法", "issues": exc.errors})
            _carry_done(plan, rebuilt)
            plan = rebuilt
            changed = [item.id for item in _items(plan)]
            detail = reason or "按新阶段表完整重规划"
        else:
            plan, changed, detail = apply_scope(
                plan, scope, item_id=item_id or None, reason=reason
            )

        state.plan_version = plan.plan_version
        state.replan_count += 1
        state.status = "running"
        state.remaining = [i.id for i in _items(plan) if i.status == "pending"]
        await _persist(store, state, plan)
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
            },
        )

        should_execute = cfg_now.exec_enabled if execute is None else bool(execute)
        if should_execute and not cfg_now.exec_enabled:
            should_execute = False
        result = await _run(runtime, plan, state, should_execute)
        await _record_sample(store, plan, result)
        return _finish(
            runtime,
            plan=plan,
            state=state,
            result=result,
            executed=should_execute,
            warnings=plan_warnings(plan, cfg_now),
        )

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


async def _persist(store: Any, state: RunState, plan: SchedulePlan) -> None:
    if store is None:
        return
    try:
        await store.setup()
        await store.create_run(state)
        await store.save_plan(plan)
    except Exception:
        logger.exception("调度运行落库失败，继续内存执行")


async def _load(runtime: ToolRuntime, run_id: str) -> Optional[tuple[RunState, SchedulePlan]]:
    snapshot = (getattr(runtime, "state", {}) or {}).get("scheduler") or {}
    if str(snapshot.get("run_id", "")) == run_id and snapshot.get("plan"):
        try:
            return (
                RunState.model_validate(snapshot["state"]),
                SchedulePlan.model_validate(snapshot["plan"]),
            )
        except Exception:
            logger.exception("图状态中的调度快照无法解析，尝试数据库")
    try:
        from scheduler.store import SchedulerStore

        store = SchedulerStore()
        row = await store.get_run(run_id)
        plan_row = await store.latest_plan(run_id)
        if row and plan_row:
            return (
                RunState.model_validate(row["state"]),
                SchedulePlan.model_validate(plan_row),
            )
    except Exception:
        logger.exception("从数据库恢复调度运行失败")
    return None


async def _record_sample(store: Any, plan: SchedulePlan, result: Any) -> None:
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
    except Exception:
        logger.exception("调度样本落库失败，已忽略")


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
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "status": item.status,
                        "depends_on": item.depends_on,
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
        "done": list(result.done) if result else [],
        "failed": list(result.failed) if result else [],
        "skipped": list(result.skipped) if result else [],
        "checkpoints": result.checkpoints if result else 0,
        "replans": result.replans if result else 0,
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


def _carry_done(old: SchedulePlan, new: SchedulePlan) -> None:
    """完整重规划时保留已完成条目，避免already-done 工作被重做。"""
    done = {
        item.id: item
        for item in _items(old)
        if item.status in ("done", "skipped")
    }
    for item in _items(new):
        previous = done.get(item.id)
        if previous is not None:
            item.status = previous.status  # type: ItemStatus
            item.summary = previous.summary
            item.finished_at = previous.finished_at


def _node_prompt(item: PlanItem, state: RunState) -> str:
    lines = [
        "你在执行一个已调度任务节点。不要重新规划，不要调用 scheduler_plan。",
        f"总目标：{state.goal}",
        f"阶段：{item.phase_id}",
        f"条目：{item.title}",
        f"说明：{item.instruction}",
    ]
    if item.acceptance:
        lines.append(f"完成标准：{item.acceptance}")
    if state.summary:
        lines.append(f"已完成摘要：\n{state.summary[-3000:]}")
    if item.writes:
        lines.append(f"允许写入：{', '.join(item.writes)}")
    lines.append("只完成该条目，不要展开其他条目；完成后用 1-3 句话总结产出。")
    return "\n".join(lines)


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


__all__ = ["SchedulerPlanInput", "SchedulerReplanInput", "create_scheduler_tools"]
