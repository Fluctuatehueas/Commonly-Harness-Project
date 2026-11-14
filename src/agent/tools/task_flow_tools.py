"""任务流步骤上报工具。

命中任务流后，步骤进度由主 Agent 主动上报：工具把状态写回图状态
``task_flow``（随 checkpoint 持久化，HITL 恢复后仍可继续），同时通过
LangGraph 的 custom stream 通道推送前端执行卡片事件。

事件契约与旧执行器保持一致，前端 ``stores/chat.ts`` 无需改动：

    {"type": "task_flow_progress", "status": "step_started|step_token|step_done|step_failed",
     "flow_id": "...", "flow_name": "...", "step_id": "...", "branch": "..."}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from task_flow.models import utcnow

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 20_000


class TaskFlowStepUpdateInput(BaseModel):
    """任务流步骤上报参数。"""

    step_id: str = Field(description="任务流步骤 ID，例如 s1；必须是任务流里列出的 id")
    status: Literal["running", "done", "failed", "skipped"] = Field(
        description="步骤状态：running 开始执行，done 完成，failed 失败，skipped 跳过"
    )
    summary: str = Field(default="", description="该步骤的产出摘要，done / failed 时填写")


def _emit(
    runtime: ToolRuntime,
    flow: dict[str, Any],
    step: dict[str, Any],
    status: str,
    summary: str,
) -> None:
    """把步骤状态推给前端执行卡片；没有 custom 流时静默跳过。"""
    writer = getattr(runtime, "stream_writer", None)
    if writer is None:
        return
    base = {
        "type": "task_flow_progress",
        "flow_id": flow.get("flow_id", ""),
        "flow_name": flow.get("flow_name", ""),
        "step_id": step.get("id", ""),
        "branch": step.get("branch"),
        "attempts": 1,
    }
    try:
        if status == "running":
            writer({**base, "status": "step_started"})
        elif status == "done":
            if summary:
                writer({**base, "status": "step_token", "delta": summary})
            writer({**base, "status": "step_done"})
        elif status == "failed":
            writer({**base, "status": "step_failed"})
    except Exception:  # noqa: BLE001 - 事件推送失败不影响工具结果
        pass


@tool(args_schema=TaskFlowStepUpdateInput)
def task_flow_step_update(
    step_id: str,
    status: Literal["running", "done", "failed", "skipped"],
    summary: str = "",
    runtime: ToolRuntime = None,  # type: ignore[assignment]
) -> Command | str:
    """上报任务流步骤状态；仅在按任务流执行时使用。

    每开始一步调用一次 status="running"，完成时调用 status="done" 并附产出摘要。
    没有命中任务流时调用会返回错误提示，不需要上报。

    本工具**永不抛异常**：进度上报只是旁路信息，失败时返回可读错误让 Agent
    继续完成剩下的步骤，不能因为一条卡片状态没记上就中断整轮任务。
    """
    try:
        return _apply_step_update(step_id, status, summary, runtime)
    except Exception as exc:  # noqa: BLE001 - 上报失败不能打断任务执行
        logger.warning(
            "event=taskflow.step.update.failed step_id=%s status=%s",
            step_id,
            status,
            exc_info=True,
        )
        return json.dumps(
            {
                "ok": False,
                "error": f"步骤状态上报失败，请继续执行后续步骤：{exc}",
            },
            ensure_ascii=False,
        )


def _apply_step_update(
    step_id: str,
    status: str,
    summary: str,
    runtime: ToolRuntime,
) -> Command | str:
    """真正的上报逻辑；异常由调用方统一兜底。"""
    state = getattr(runtime, "state", None)
    flow = dict(state.get("task_flow") or {}) if isinstance(state, dict) else {}
    steps = [dict(item) for item in (flow.get("steps") or [])]
    if not flow or not steps:
        return json.dumps(
            {"ok": False, "error": "当前没有命中的任务流，无需上报步骤"},
            ensure_ascii=False,
        )

    target = next((item for item in steps if item.get("id") == step_id), None)
    if target is None:
        return json.dumps(
            {
                "ok": False,
                "error": f"未知步骤 id：{step_id}",
                "valid_ids": [item.get("id") for item in steps],
            },
            ensure_ascii=False,
        )

    target["status"] = status
    if summary:
        target["summary"] = summary[:SUMMARY_MAX_CHARS]
    # 步骤自带起止时间：开始记录 started_at，结束记录 finished_at。
    stamp = utcnow().isoformat()
    if status == "running":
        target["started_at"] = stamp
        # 重跑同一步时清掉上一次的结束时间，避免时间区间自相矛盾。
        target["finished_at"] = ""
    elif status in {"done", "failed", "skipped"}:
        target["finished_at"] = stamp
    flow["steps"] = steps
    flow["current_step"] = step_id if status == "running" else ""

    logger.info(
        "event=taskflow.step.update flow_id=%s step_id=%s status=%s",
        flow.get("flow_id", ""),
        step_id,
        status,
    )
    _emit(runtime, flow, target, status, summary)

    pending = [item.get("id") for item in steps if item.get("status") == "pending"]
    payload = json.dumps(
        {
            "ok": True,
            "step_id": step_id,
            "status": status,
            "updated_at": stamp,
            "pending_steps": pending,
        },
        ensure_ascii=False,
    )
    tool_call_id = getattr(runtime, "tool_call_id", None)
    if not tool_call_id:
        return payload
    return Command(
        update={
            "task_flow": flow,
            "messages": [ToolMessage(payload, tool_call_id=tool_call_id)],
        }
    )


def create_task_flow_tools() -> list[Any]:
    """返回主 Agent 使用的任务流工具。"""
    return [task_flow_step_update]


__all__ = ["create_task_flow_tools", "task_flow_step_update"]
