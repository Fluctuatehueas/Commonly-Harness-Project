"""计划器：把任务流或模型提交的两级表转换成可执行的 SchedulePlan。

纯函数，不调用模型、不访问数据库，便于单测。

- REFERENCE：TaskFlow 结构转换，不做语义改写；
- DYNAMIC：MD 只展开前两个阶段，其余留在 ``pending_phases``；HD 完整展开。
"""

from __future__ import annotations

from typing import Any, Optional

from .config import TierConfig
from .models import (
    PlanItem,
    PlanItemInput,
    PlanMethod,
    PlanMethodInput,
    PlanPhase,
    PlanPhaseInput,
    SchedulePlan,
    Tier,
    utcnow_iso,
)


class PlanError(ValueError):
    """计划不合法；errors 供工具直接回传给模型修正。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("；".join(errors))


def _item_status_default(item: PlanItemInput, phase_id: str) -> PlanItem:
    return PlanItem(
        id=item.id,
        phase_id=phase_id,
        title=item.title,
        instruction=item.instruction or item.title,
        owner=item.owner,
        depends_on=list(item.depends_on),
        reads=list(item.reads),
        writes=list(item.writes),
        acceptance=item.acceptance,
        impact=item.impact,
        uncertainty=item.uncertainty,
        irreversible=item.irreversible,
        checkpoint=item.checkpoint,
    )


def _all_items(phases: list[PlanPhase]) -> list[PlanItem]:
    return [item for phase in phases for item in phase.items]


def _normalize_phase(phase: PlanPhaseInput) -> PlanPhase:
    """把阶段入参转成计划模型：没有 methods 时合成一个默认方法。"""
    methods_input = list(phase.methods)
    if not methods_input:
        methods_input = [
            PlanMethodInput(id=f"{phase.id}.m0", title=phase.title, items=list(phase.items))
        ]
    methods = [
        PlanMethod(
            id=method.id,
            title=method.title or phase.title,
            reason=method.reason,
            precondition=method.precondition,
            items=[_item_status_default(item, phase.id) for item in method.items],
        )
        for method in methods_input
    ]
    active = methods[0]
    return PlanPhase(
        id=phase.id,
        title=phase.title,
        goal=phase.goal,
        items=[item.model_copy(deep=True) for item in active.items],
        methods=methods,
        active_method_id=active.id,
        tried_methods=[active.id],
    )


def topological_waves(items: list[PlanItem]) -> tuple[list[list[PlanItem]], list[str]]:
    """按 depends_on 计算波次；返回（波次, 错误）。"""
    by_id = {item.id: item for item in items}
    if len(by_id) != len(items):
        return [], ["条目 ID 存在重复"]
    for item in items:
        missing = [dep for dep in item.depends_on if dep not in by_id]
        if missing:
            return [], [f"{item.id} 依赖了不存在的条目：{', '.join(missing)}"]

    remaining = {item.id: set(item.depends_on) for item in items}
    waves: list[list[PlanItem]] = []
    while remaining:
        ready = sorted(name for name, deps in remaining.items() if not deps)
        if not ready:
            return [], ["条目之间存在循环依赖"]
        waves.append([by_id[name] for name in ready])
        for name in ready:
            remaining.pop(name)
        for deps in remaining.values():
            deps.difference_update(ready)
    return waves, []


def _serialize_conflicts(waves: list[list[PlanItem]]) -> list[dict[str, Any]]:
    """同一波次内写路径重叠时自动串行化：后一个条目依赖前一个。

    直接拒绝会让模型反复重排；串行化保持确定性，并把决策记录下来。
    """
    resolved: list[dict[str, Any]] = []
    changed = True
    while changed:
        changed = False
        for wave in waves:
            owners: dict[str, list[str]] = {}
            for item in wave:
                for path in item.writes:
                    owners.setdefault(path, []).append(item.id)
            conflicted = {
                path: ids for path, ids in owners.items() if len(ids) > 1
            }
            if not conflicted:
                continue
            by_id = {item.id: item for item in wave}
            for path, ids in conflicted.items():
                for later, earlier in zip(ids[1:], ids[:-1]):
                    target = by_id[later]
                    if earlier not in target.depends_on:
                        target.depends_on.append(earlier)
                        changed = True
                resolved.append(
                    {"path": path, "items": ids, "resolved": "serialized"}
                )
        if changed:
            waves, _ = topological_waves([item for wave in waves for item in wave])
    return resolved


def _validate(phases: list[PlanPhase], cfg: TierConfig) -> list[str]:
    errors: list[str] = []
    items = _all_items(phases)
    if not items:
        errors.append("计划至少需要一个条目")
    if len(items) > cfg.max_items:
        errors.append(f"条目总数 {len(items)} 超过上限 {cfg.max_items}")
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        errors.append("条目 ID 存在重复")
    phase_ids = [phase.id for phase in phases]
    if len(phase_ids) != len(set(phase_ids)):
        errors.append("阶段 ID 存在重复")
    for phase in phases:
        if not phase.items:
            errors.append(f"阶段 {phase.id} 没有条目")
        method_ids = [method.id for method in phase.methods]
        if len(method_ids) != len(set(method_ids)):
            errors.append(f"阶段 {phase.id} 的方法 ID 存在重复")
        for method in phase.methods:
            if not method.items:
                errors.append(f"阶段 {phase.id} 的方法 {method.id} 没有条目")
    return errors


def _link_phases_sequential(phases: list[PlanPhase]) -> None:
    """MD / REFERENCE：后一阶段依赖前一阶段全部条目，保证阶段串行。"""
    for previous, current in zip(phases, phases[1:]):
        previous_ids = [item.id for item in previous.items]
        for item in current.items:
            for dep in previous_ids:
                if dep not in item.depends_on:
                    item.depends_on.append(dep)


def build_plan(
    *,
    run_id: str,
    goal: str,
    tier: Tier,
    phases: list[PlanPhaseInput],
    cfg: TierConfig,
    mode: str = "dynamic",
    flow_id: Optional[str] = None,
) -> SchedulePlan:
    """校验并构建计划。MD 只展开前两个阶段，其余进入 pending_phases。"""
    if tier is Tier.EZ:
        raise PlanError(["EZ 档不需要调度计划，直接执行主 Agent ReAct 循环"])

    normalized = [_normalize_phase(phase) for phase in phases]
    if not normalized:
        raise PlanError(["阶段列表为空"])

    active = normalized
    pending: list[PlanPhase] = []
    if tier is Tier.MD and len(normalized) > 2:
        active, pending = normalized[:2], normalized[2:]

    errors = _validate(active, cfg)
    if errors:
        raise PlanError(errors)
    total_items = len(_all_items(active)) + len(_all_items(pending))
    if total_items > cfg.max_items:
        errors.append(f"条目总数 {total_items} 超过上限 {cfg.max_items}")
    if errors:
        raise PlanError(errors)
    if pending:
        pending_errors = _validate(pending, cfg)
        if pending_errors:
            raise PlanError([f"待展开阶段：{msg}" for msg in pending_errors])

    if mode == "reference" or tier is Tier.MD:
        _link_phases_sequential(active)

    items = _all_items(active)
    waves, wave_errors = topological_waves(items)
    if wave_errors:
        raise PlanError(wave_errors)
    conflicts = _serialize_conflicts(waves)
    waves, wave_errors = topological_waves(items)
    if wave_errors:
        raise PlanError(wave_errors)

    return SchedulePlan(
        run_id=run_id,
        goal=goal,
        mode="reference" if mode == "reference" else "dynamic",
        tier=tier,
        flow_id=flow_id,
        phases=active,
        pending_phases=pending,
        waves=[[item.id for item in wave] for wave in waves],
        conflicts=conflicts,
        created_at=utcnow_iso(),
    )


def build_reference_plan(
    *,
    run_id: str,
    flow: Any,
    cfg: TierConfig,
    tier: Tier = Tier.HD,
) -> SchedulePlan:
    """把 TaskFlow 结构转换成计划：主流为 P0，每条并行子流为一个阶段。"""
    phases: list[PlanPhaseInput] = []
    main_items: list[PlanItemInput] = []
    branch_phases: list[PlanPhaseInput] = []

    for step in getattr(flow, "steps", []) or []:
        main_items.append(
            PlanItemInput(
                id=str(step.id),
                title=str(step.name),
                instruction=str(getattr(step, "instruction", "") or step.name),
                owner=str(getattr(step, "executor", "auto") or "auto"),
                depends_on=[main_items[-1].id] if main_items else [],
                acceptance=str(getattr(step, "constraints", "") or ""),
                checkpoint=bool(getattr(step, "checkpoint", False)),
            )
        )
        for index, subflow in enumerate(getattr(step, "parallel", None) or [], start=1):
            branch_items: list[PlanItemInput] = []
            for branch_step in getattr(subflow, "steps", []) or []:
                branch_items.append(
                    PlanItemInput(
                        id=str(branch_step.id),
                        title=str(branch_step.name),
                        instruction=str(
                            getattr(branch_step, "instruction", "") or branch_step.name
                        ),
                        owner=str(getattr(branch_step, "executor", "auto") or "auto"),
                        depends_on=[branch_items[-1].id] if branch_items else [],
                        acceptance=str(getattr(branch_step, "constraints", "") or ""),
                        checkpoint=bool(getattr(branch_step, "checkpoint", False)),
                    )
                )
            if branch_items:
                branch_phases.append(
                    PlanPhaseInput(
                        id=f"P{len(phases) + len(branch_phases) + 1}.{index}",
                        title=str(getattr(subflow, "name", "") or f"{step.name} 分支 {index}"),
                        items=branch_items,
                    )
                )

    if main_items:
        phases.append(PlanPhaseInput(id="P0", title="主流程", items=main_items))
    phases.extend(branch_phases)
    if not phases:
        raise PlanError(["任务流没有可执行步骤"])

    plan = build_plan(
        run_id=run_id,
        goal=str(getattr(flow, "name", "") or "任务流执行"),
        tier=tier,
        phases=phases,
        cfg=cfg,
        mode="reference",
        flow_id=str(getattr(flow, "id", "") or ""),
    )
    return plan


def plan_warnings(plan: SchedulePlan, cfg: TierConfig) -> list[str]:
    warnings: list[str] = []
    alternatives = sum(
        max(0, len(phase.methods) - 1) for phase in plan.phases + plan.pending_phases
    )
    if alternatives:
        warnings.append(f"共 {alternatives} 个候选方法，失败时会按顺序换方法")
    if len(plan.waves) > cfg.wave_depth_warn:
        warnings.append(
            f"依赖深度 {len(plan.waves)} 超过 {cfg.wave_depth_warn}，建议重新划分阶段"
        )
    if plan.pending_phases:
        warnings.append(
            f"{len(plan.pending_phases)} 个阶段尚未展开，完成当前阶段后由重规划推进"
        )
    return warnings


__all__ = [
    "PlanError",
    "build_plan",
    "build_reference_plan",
    "plan_warnings",
    "topological_waves",
]
