"""重规划：reorder / subtree / full 三种范围下的确定性计划变换。

只改计划结构，不执行节点；已完成的条目永不回退。
"""

from __future__ import annotations

from .models import PlanItem, ReplanScope, SchedulePlan, Tier
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


def reorder(
    plan: SchedulePlan, item_id: str | None = None, reason: str = ""
) -> tuple[SchedulePlan, list[str], str]:
    """只调整同波次内顺序：给定 item_id 时把它提前到所在波次最前。

    不改依赖、不重算波次，否则排序会被 depends_on 覆盖回去。
    """
    updated = plan.model_copy(deep=True)
    changed: list[str] = []
    if item_id:
        for wave in updated.waves:
            if item_id in wave and wave[0] != item_id:
                wave.remove(item_id)
                wave.insert(0, item_id)
                changed = list(wave)
                break
    if not changed:
        return updated, [], reason or "没有可调整的同波次条目"
    updated.plan_version += 1
    return updated, changed, reason or f"将 {item_id} 提前到波次最前"


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


def _drop_dangling_deps(plan: SchedulePlan) -> None:
    """换方法后清理指向已删除条目的依赖，避免波次计算报错。"""
    ids = {item.id for item in _all_items(plan)}
    for item in _all_items(plan):
        item.depends_on = [dep for dep in item.depends_on if dep in ids]


def has_untried_method(plan: SchedulePlan, phase_id: str) -> bool:
    phase = next((p for p in plan.phases if p.id == phase_id), None)
    if phase is None:
        return False
    return any(method.id not in phase.tried_methods for method in phase.methods)


def switch_method(
    plan: SchedulePlan, phase_id: str, *, reason: str = ""
) -> tuple[SchedulePlan, list[str], str, bool]:
    """切到该阶段下一个未试过的方法：已完成叶子保留，其余条目被替换。"""
    updated = plan.model_copy(deep=True)
    phase = next((p for p in updated.phases if p.id == phase_id), None)
    if phase is None:
        return plan, [], f"阶段 {phase_id} 不存在", False
    candidate = next(
        (method for method in phase.methods if method.id not in phase.tried_methods),
        None,
    )
    if candidate is None:
        return plan, [], f"阶段 {phase_id} 没有可用方法", False

    done_items = [item for item in phase.items if item.status == "done"]
    done_ids = {item.id for item in done_items}
    new_items = [
        blueprint.model_copy(deep=True)
        for blueprint in candidate.items
        if blueprint.id not in done_ids
    ]
    phase.items = done_items + new_items
    phase.active_method_id = candidate.id
    phase.tried_methods.append(candidate.id)

    _drop_dangling_deps(updated)
    if updated.mode == "reference" or updated.tier is Tier.MD:
        _link_phases_sequential(updated.phases)
    waves, errors = topological_waves(_all_items(updated))
    if not errors:
        updated.waves = [[item.id for item in wave] for wave in waves]
    updated.plan_version += 1
    detail = reason or f"阶段 {phase.id} 换方法：{candidate.title or candidate.id}"
    return updated, [item.id for item in new_items], detail, True


def revision_targets(plan: SchedulePlan, item_id: str) -> list[str]:
    """会被 revise 重开的条目：目标本身与状态不是 pending 的下游。"""
    targets = _downstream(plan, item_id)
    return [
        item.id
        for item in _all_items(plan)
        if item.id in targets and item.status != "pending"
    ]


def revise(
    plan: SchedulePlan, item_id: str, *, reason: str = ""
) -> tuple[SchedulePlan, list[str], str]:
    """验收失败后重开条目及其全部下游；已完成的下游也要重新验收。"""
    updated = plan.model_copy(deep=True)
    targets = _downstream(updated, item_id)
    changed: list[str] = []
    for item in _all_items(updated):
        if item.id not in targets or item.status == "pending":
            continue
        item.status = "pending"
        item.error = ""
        item.summary = ""
        item.finished_at = None
        changed.append(item.id)
    _refresh_waves(updated)
    updated.plan_version += 1
    return updated, changed, reason or f"重开 {item_id} 及其下游"


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
    return reorder(plan, item_id, reason)


__all__ = [
    "apply_scope",
    "full",
    "has_untried_method",
    "promote_pending",
    "reorder",
    "revise",
    "revision_targets",
    "subtree",
    "switch_method",
]
