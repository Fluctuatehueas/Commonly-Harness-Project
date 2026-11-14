"""Agent 图状态中的 PDCA 与任务流字段。

计划写入图状态而不是只放运行时上下文，目的是在 HITL 中断/恢复后仍能
拿到本轮分工清单；新一轮用户消息开始时由中间件清空。

任务流同理：命中后把流程快照写入 ``task_flow``，恢复后步骤上报工具
仍能读到流程与进度。

``task_flow`` 必须带 reducer：同一超级步内模型可能并发调用多次
``task_flow_step_update``（多个 Command 各自写该键），Latest-only 通道
会直接抛 ``InvalidUpdateError``。这里按步骤 id 做状态合并，保留推进更靠后
的一条，避免并发上报互相覆盖。
"""

from __future__ import annotations

from typing import Annotated, Any

from deepagents.graph import DeepAgentState

# 步骤状态优先级：数值越大表示推进越靠后，合并时取大者。
_STATUS_RANK = {"pending": 0, "running": 1, "done": 2, "failed": 2, "skipped": 2}
# 只由合并逻辑维护的字段，不参与浅覆盖。
_STEP_FIELD = "steps"


def merge_task_flow(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """合并两次 ``task_flow`` 写入：标量取后者，步骤按 id 取推进更靠后的状态。"""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)

    merged = {
        key: value
        for key, value in left.items()
        if key != _STEP_FIELD
    }
    for key, value in right.items():
        if key == _STEP_FIELD:
            continue
        # current_step 为空表示"没有进行中的步骤"，不能覆盖后者的有效值；
        # 其余标量（flow_id / status / started_at）以后写入者为准。
        if key == "current_step" and not value:
            continue
        merged[key] = value

    steps: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*(left.get(_STEP_FIELD) or []), *(right.get(_STEP_FIELD) or [])]:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or "")
        if not step_id:
            continue
        if step_id not in steps:
            steps[step_id] = dict(item)
            order.append(step_id)
            continue
        current = steps[step_id]
        status = str(item.get("status") or "pending")
        if _STATUS_RANK.get(status, 0) >= _STATUS_RANK.get(
            str(current.get("status") or "pending"), 0
        ):
            current["status"] = status
        if item.get("summary"):
            current["summary"] = item["summary"]

    merged[_STEP_FIELD] = [steps[step_id] for step_id in order]
    return merged


def merge_scheduler(
    current: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> dict[str, Any]:
    """调度快照合并：不同 run 直接替换；同一 run 取 plan_version 更新的快照。"""
    if not incoming:
        return current or {}
    if not current or str(incoming.get("run_id")) != str(current.get("run_id")):
        return incoming
    current_version = int((current.get("plan") or {}).get("plan_version") or 0)
    incoming_version = int((incoming.get("plan") or {}).get("plan_version") or 0)
    return incoming if incoming_version >= current_version else current


class PdcState(DeepAgentState, total=False):
    """在 DeepAgent 状态上追加计划与任务流字段。"""

    pdc_plan: str
    task_flow: Annotated[dict[str, Any], merge_task_flow]
    scheduler: Annotated[dict[str, Any], merge_scheduler]


__all__ = ["PdcState", "merge_scheduler", "merge_task_flow"]
