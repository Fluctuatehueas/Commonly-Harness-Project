"""查询远程异步任务，并将终态结果投递到所属主会话。"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from langgraph_sdk import get_client

from agent.config import ASYNC_AGENT_PROTOCOL_URL
from agent.schema import AsyncTaskStatusResponse
from api.agent_loader import agent_loader
from api.message_utils import content_to_text


router = APIRouter()

TERMINAL_RUN_STATUSES = {"success", "error", "interrupted", "cancelled", "timeout"}
TASK_ID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _get_attr(value: Any, key: str, default: Any = None) -> Any:
    """兼容 LangGraph SDK 返回的字典和对象属性访问。"""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def extract_async_task_id(value: Any) -> str | None:
    """从异步任务工具结果中提取 Agent Protocol 分配的任务线程 ID。"""
    if isinstance(value, str):
        match = TASK_ID_PATTERN.search(value)
        return match.group(0) if match else None
    if isinstance(value, list):
        for item in value:
            task_id = extract_async_task_id(item)
            if task_id:
                return task_id
    if isinstance(value, dict):
        for item in value.values():
            task_id = extract_async_task_id(item)
            if task_id:
                return task_id
    return None


def _message_role(message: Any) -> str:
    """将 SDK 消息角色归一化为页面使用的角色名称。"""
    role = _get_attr(message, "role")
    if role:
        return {"human": "user", "ai": "assistant"}.get(str(role), str(role))
    message_type = str(_get_attr(message, "type", type(message).__name__)).lower()
    if "human" in message_type:
        return "user"
    if "tool" in message_type:
        return "tool"
    return "assistant"


def _extract_task_output(values: Any) -> str:
    """提取异步任务最后一条助手文本。"""
    if not isinstance(values, dict):
        return ""
    messages = values.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if _message_role(message) != "assistant":
            continue
        content = content_to_text(_get_attr(message, "content", "")).strip()
        if content:
            return content
    return ""


def _extract_error(run: Any, state: Any) -> str | None:
    """从运行元数据或线程任务中提取可供用户查看的失败原因。"""
    metadata = _get_attr(run, "metadata", {}) or {}
    if isinstance(metadata, dict):
        for key in ("error", "exception", "message"):
            if metadata.get(key):
                return str(metadata[key])
    for task in _get_attr(state, "tasks", []) or []:
        error = _get_attr(task, "error")
        if error:
            return str(error)
    return None


@router.get("/async-tasks/{task_id}", response_model=AsyncTaskStatusResponse)
async def get_async_task_status(
    task_id: str,
    user_id: str = Query(..., min_length=1),
) -> AsyncTaskStatusResponse:
    """返回任务状态；终态结果写入主会话并返回结果文本。"""
    binding = await agent_loader.get_async_task_binding(task_id)
    if binding is None or binding.user_id != user_id:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    await agent_loader.require_session(user_id, binding.thread_id)

    client = get_client(url=ASYNC_AGENT_PROTOCOL_URL)
    try:
        runs = await client.runs.list(task_id, limit=10)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="无法查询异步子 Agent 任务，请稍后重试",
        ) from exc

    if not runs:
        return AsyncTaskStatusResponse(task_id=task_id, status="pending", done=False)

    latest_run = runs[0]
    status = str(_get_attr(latest_run, "status", "unknown")).lower()
    status = {
        "failed": "error",
        "canceled": "cancelled",
        "timed_out": "timeout",
    }.get(status, status)
    is_terminal = status in TERMINAL_RUN_STATUSES
    if not is_terminal:
        return AsyncTaskStatusResponse(
            task_id=task_id,
            status=status,
            done=False,
            run_id=_get_attr(latest_run, "run_id"),
        )

    state = None
    try:
        state = await client.threads.get_state(task_id)
        values = _get_attr(state, "values", {})
        content = _extract_task_output(values)
    except Exception as exc:
        # run 成功不代表已读到结果；失败必须可重试，不能写入占位成功消息。
        raise HTTPException(
            status_code=502, detail="无法读取异步任务结果，请稍后重试",
        ) from exc

    error = None
    if status == "success":
        if not content:
            raise HTTPException(
                status_code=502,
                detail="异步任务未返回可交付结果，请稍后重试",
            )
        main_message = content
    else:
        error = _extract_error(latest_run, state) or {
            "timeout": "后台任务超时。",
            "cancelled": "后台任务已取消。",
            "interrupted": "后台任务已中断。",
        }.get(status, "后台任务未能完成。")
        main_message = f"后台任务未完成：{error}"

    try:
        delivered = await agent_loader.publish_async_task_result(
            task_id, content=main_message,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="无法将异步任务结果写入主会话，请稍后重试",
        ) from exc

    return AsyncTaskStatusResponse(
        task_id=task_id,
        status=status,
        done=True,
        delivered=delivered,
        result=main_message,
        error=error,
        run_id=_get_attr(latest_run, "run_id"),
        updated_at=str(_get_attr(latest_run, "updated_at", "")) or None,
    )
