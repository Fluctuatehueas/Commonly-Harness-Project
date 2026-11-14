"""重规划：reorder / subtree / full 三种范围下的确定性计划变换。

只改计划结构，不执行节点；已完成的条目永不回退。
"""

from __future__ import annotations

from .models import PlanItem, ReplanScope, SchedulePlan
from .planner import _link_phases_sequential, topological_waves


def _all_items(plan: SchedulePlan) -> list[PlanItem]:
    return [item for phase in plan.phases for item in phase.items]


def _refresh_waves(plan: SchedulePlan) -> SchedulePlan:
    waves, errors = topological_waves(_all_items(plan))
    if errors:
        # 结构由本模块维护，理论上不会成环；保留旧波次比抛错更安全。
        return plan
    plan.waves = [[item.id for item in wave] for wave in waves]
    return plan


def _downstream(plan: SchedulePlan, item_id: str) -> set[str]:
    """返回 item_id 及其全部下游条目。"""
    children: dict[str, set[str]] = {}
    for item in _all_items(plan):
        for dep in item.depends_on:
            children.setdefault(dep, set()).add(item.id)
    seen: set[str] = set()
    stack = [item_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, set()))
    return seen


def reorder(plan: SchedulePlan) -> tuple[SchedulePlan, list[str], str]:
    """只重算波次，不改结构。"""
    updated = plan.model_copy(deep=True)
    before = [list(wave) for wave in updated.waves]
    _refresh_waves(updated)
    changed = sorted({item for wave in updated.waves for item in wave}) if updated.waves != before else []
    updated.plan_version += 1
    return updated, changed, "重新计算波次，未改变条目依赖"


def subtree(plan: SchedulePlan, item_id: str, reason: str = "") -> tuple[SchedulePlan, list[str], str]:
    """重置失败条目及其下游；已完成条目保持完成。"""
    updated = plan.model_copy(deep=True)
    targets = _downstream(updated, item_id)
    changed: list[str] = []
    for item in _all_items(updated):
        if item.id not in targets or item.status == "done":
            continue
        if item.status != "pending":
            item.status = "pending"
            item.error = ""
            item.summary = ""
            item.finished_at = None
            changed.append(item.id)
    _refresh_waves(updated)
    updated.plan_version += 1
    return updated, changed, reason or f"重置 {item_id} 及其下游"


def full(plan: SchedulePlan, reason: str = "") -> tuple[SchedulePlan, list[str], str]:
    """保留已完成，其余全部回到 pending，并重算波次。"""
    updated = plan.model_copy(deep=True)
    changed: list[str] = []
    for item in _all_items(updated):
        if item.status == "done":
            continue
        if item.status != "pending":
            item.status = "pending"
            item.error = ""
            item.summary = ""
            item.started_at = None
            item.finished_at = None
            changed.append(item.id)
    _refresh_waves(updated)
    updated.plan_version += 1
    return updated, changed, reason or "完整重规划：保留已完成条目"


def promote_pending(plan: SchedulePlan) -> tuple[SchedulePlan, list[str], str]:
    """MD 完成当前阶段后展开下一个待执行阶段。"""
    if not plan.pending_phases:
        return plan, [], ""
    updated = plan.model_copy(deep=True)
    phase = updated.pending_phases.pop(0)
    updated.phases.append(phase)
    _link_phases_sequential(updated.phases)
    _refresh_waves(updated)
    updated.plan_version += 1
    changed = [item.id for item in phase.items]
    return updated, changed, f"展开阶段 {phase.id}：{phase.title}"


def apply_scope(
    plan: SchedulePlan,
    scope: ReplanScope,
    *,
    item_id: str | None = None,
    reason: str = "",
) -> tuple[SchedulePlan, list[str], str]:
    """按范围分派；未知范围退化为 reorder。"""
    if scope == "subtree" and item_id:
        return subtree(plan, item_id, reason)
    if scope == "full":
        return full(plan, reason)
    return reorder(plan)


__all__ = [
    "apply_scope",
    "full",
    "promote_pending",
    "reorder",
    "subtree",
]
