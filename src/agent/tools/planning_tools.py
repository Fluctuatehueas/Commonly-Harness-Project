"""多智能体分工清单工具。

主 Agent 面对复杂任务时，先调用本工具产出可执行的分工清单，再按
``parallel_waves`` 并发委派。工具本身不执行任务，只做三件事：

1. 把任务拆成带负责人（main / 子 Agent 名称）的工作项；
2. 按 ``depends_on`` 计算执行波次（同一波次可以并行）；
3. 检查同一波次内是否存在写冲突（同一路径被多个工作项写）。

输出是结构化 JSON，模型可以直接照着它发多个 ``task`` 调用。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from pydantic import BaseModel, Field

from agent.time_context import now_iso, timezone_name

MAX_TASKS = 24


class WorkItem(BaseModel):
    """一个工作项。"""

    id: str = Field(description="工作项唯一 ID，例如 research、deps")
    title: str = Field(description="一句话说明要做什么")
    owner: str = Field(
        description="负责人：main 或具体子 Agent 名称（如 web_researcher / parallel_executor）"
    )
    depends_on: list[str] = Field(default_factory=list, description="依赖的工作项 ID")
    reads: list[str] = Field(default_factory=list, description="会读取的路径或资源")
    writes: list[str] = Field(default_factory=list, description="会写入的路径或资源")
    notes: str = Field(default="", description="边界、产物或注意事项")


class WorkPlanInput(BaseModel):
    goal: str = Field(description="本轮总目标")
    tasks: list[WorkItem] = Field(description="工作项列表")


def _topological_waves(tasks: list[WorkItem]) -> tuple[list[list[WorkItem]], list[str]]:
    """按依赖计算并行波次；返回（波次列表, 错误列表）。"""
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        return [], ["工作项 ID 存在重复"]
    for task in tasks:
        missing = [dep for dep in task.depends_on if dep not in by_id]
        if missing:
            return [], [f"{task.id} 依赖了不存在的工作项：{', '.join(missing)}"]

    remaining = {task.id: set(task.depends_on) for task in tasks}
    waves: list[list[WorkItem]] = []
    while remaining:
        ready = sorted(item for item, deps in remaining.items() if not deps)
        if not ready:
            return [], ["工作项之间存在循环依赖"]
        waves.append([by_id[item] for item in ready])
        for item in ready:
            remaining.pop(item)
        for deps in remaining.values():
            deps.difference_update(ready)
    return waves, []


def _wave_conflicts(waves: list[list[WorkItem]]) -> list[dict[str, Any]]:
    """同一波次里，多个工作项写同一路径视为冲突，需要拆到不同波次。"""
    conflicts: list[dict[str, Any]] = []
    for index, wave in enumerate(waves, start=1):
        owners: dict[str, list[str]] = {}
        for task in wave:
            for path in task.writes:
                owners.setdefault(path, []).append(task.id)
        for path, items in owners.items():
            if len(items) > 1:
                conflicts.append({"wave": index, "path": path, "items": items})
    return conflicts


@tool(args_schema=WorkPlanInput)
def create_work_plan(
    goal: str,
    tasks: list[WorkItem],
    runtime: ToolRuntime,
) -> str:
    """产出多智能体分工清单，并计算可并行的执行波次。

    Args:
        goal: 本轮总目标。
        tasks: 工作项列表；每项要写清负责人、依赖、读写路径。

    Returns:
        JSON 字符串：{ok, goal, waves, conflicts, guidance, error}
    """
    if not tasks:
        return json.dumps(
            {"ok": False, "error": "至少需要一个工作项", "goal": goal},
            ensure_ascii=False,
        )
    if len(tasks) > MAX_TASKS:
        return json.dumps(
            {"ok": False, "error": f"工作项超过上限 {MAX_TASKS}", "goal": goal},
            ensure_ascii=False,
        )

    waves, errors = _topological_waves(tasks)
    if errors:
        return json.dumps(
            {"ok": False, "goal": goal, "error": "；".join(errors)},
            ensure_ascii=False,
        )

    conflicts = _wave_conflicts(waves)
    payload = {
        "ok": True,
        "goal": goal,
        # 计划自身带时间属性：后续回看计划时能知道它是什么时候排出来的。
        "created_at": now_iso(),
        "timezone": timezone_name(),
        "waves": [
            {
                "wave": index,
                "parallel": True,
                "items": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "owner": task.owner,
                        "reads": task.reads,
                        "writes": task.writes,
                        "notes": task.notes,
                    }
                    for task in wave
                ],
            }
            for index, wave in enumerate(waves, start=1)
        ],
        "conflicts": conflicts,
        "guidance": (
            "同一 wave 内的工作项必须放在同一条消息里并发委派；"
            "有 depends_on 的工作项等上一波完成后再委派；"
            "conflicts 中列出的写冲突必须拆到不同 wave，或改由一个实例串行完成。"
        ),
        "error": None,
    }
    # 计划同时写进图状态与本轮上下文：状态用于 HITL 恢复，上下文用于快速校验。
    payload_text = json.dumps(payload, ensure_ascii=False)
    state = getattr(runtime, "state", None)
    if isinstance(state, dict):
        try:
            state["pdc_plan"] = payload_text
        except Exception:  # noqa: BLE001 - 状态不可写时不影响规划结果
            pass
    context = getattr(runtime, "context", None)
    if context is not None:
        try:
            setattr(context, "pdc_plan", payload_text)
        except Exception:  # noqa: BLE001 - 上下文不可写时不影响规划结果
            pass
    return payload_text


def create_planning_tools() -> list[Any]:
    """返回主 Agent 使用的分工规划工具。"""
    return [create_work_plan]


__all__ = ["create_planning_tools", "create_work_plan"]
