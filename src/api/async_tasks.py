"""查询远程异步任务，并将终态结果投递到所属主会话。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from langgraph_sdk import get_client

from agent.config import ASYNC_AGENT_PROTOCOL_URL
from agent.schema import AsyncTaskStatusResponse
from api.agent_loader import agent_loader
from api.message_utils import content_to_text


router = APIRouter()
logger = logging.getLogger(__name__)

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


def _status_from_run(run: Any) -> str:
    status = str(_get_attr(run, "status", "unknown")).lower()
    return {
        "failed": "error",
        "canceled": "cancelled",
        "timed_out": "timeout",
    }.get(status, status)


async def _latest_run(client: Any, task_id: str) -> Any | None:
    runs = await client.runs.list(task_id, limit=10)
    return runs[0] if runs else None


async def _inspect_binding(
    binding: dict[str, Any],
    *,
    user_id: str,
    thread_id: str,
    client: Any,
    deliver: bool,
) -> dict[str, Any]:
    """读取一个绑定任务的状态；终态时幂等投递结果到主会话。"""
    task_id = str(binding.get("task_id") or "")
    row: dict[str, Any] = {
        "task_id": task_id,
        "agent_name": str(binding.get("agent_name") or ""),
        "status": "unknown",
        "run_id": "",
        "created_at": binding.get("created_at"),
        "done": False,
        "delivered": bool(binding.get("delivered")),
        "error": None,
    }
    if row["delivered"]:
        row["status"] = str(binding.get("status") or "done")
        row["done"] = True
        return row
    try:
        run = await asyncio.wait_for(_latest_run(client, task_id), timeout=3.0)
    except Exception:
        return row
    if run is None:
        row["status"] = "pending"
        return row

    status = _status_from_run(run)
    row["status"] = status
    row["run_id"] = str(_get_attr(run, "run_id", "") or "")
    if status not in TERMINAL_RUN_STATUSES:
        return row
    row["done"] = True
    if not deliver:
        return row

    try:
        state = await asyncio.wait_for(client.threads.get_state(task_id), timeout=3.0)
        values = _get_attr(state, "values", {})
        content = _extract_task_output(values)
        error = _extract_error(run, state)
    except Exception:
        content = ""
        error = _extract_error(run, None)
    if status == "success":
        message = content or "异步子任务已结束，但没有返回可展示的结果。"
    else:
        message = f"后台任务未完成：{error or status}"
        row["error"] = error or status
    try:
        row["delivered"] = await agent_loader.publish_async_task_result(
            task_id, content=message
        )
    except Exception:
        logger.exception("异步任务结果回写失败：%s", task_id)
        row["delivered"] = False
    if row["delivered"]:
        try:
            await agent_loader.mark_async_task_delivered(
                task_id,
                user_id=user_id,
                thread_id=thread_id,
                status=status,
            )
        except Exception:
            logger.warning("异步任务投递标记写入失败：%s", task_id, exc_info=True)
    return row


@router.get("/async-tasks")
async def list_async_tasks(
    user_id: str = Query(..., min_length=1),
    thread_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """列出会话内远程异步子任务，供输入框上方的后台任务栏展示。"""
    await agent_loader.require_session(user_id, thread_id)
    bindings = await agent_loader.list_async_task_bindings(user_id, thread_id)
    if not bindings:
        return {"tasks": []}
    client = get_client(url=ASYNC_AGENT_PROTOCOL_URL)
    rows = await asyncio.gather(
        *(
            _inspect_binding(
                binding,
                user_id=user_id,
                thread_id=thread_id,
                client=client,
                deliver=True,
            )
            for binding in bindings[:20]
        )
    )
    return {"tasks": list(rows)}


@router.post("/async-tasks/{task_id}/cancel")
async def cancel_async_task(
    task_id: str,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """终止远程异步子 Agent 的当前运行。"""
    binding = await agent_loader.get_async_task_binding(task_id)
    if binding is None or binding.user_id != user_id:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    await agent_loader.require_session(user_id, binding.thread_id)

    client = get_client(url=ASYNC_AGENT_PROTOCOL_URL)
    try:
        runs = await client.runs.list(task_id, limit=10)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="无法查询异步子 Agent 任务，请稍后重试",
        ) from exc
    if not runs:
        raise HTTPException(status_code=409, detail="任务尚未开始运行")
    latest = runs[0]
    if _status_from_run(latest) in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="任务已结束")
    run_id = str(_get_attr(latest, "run_id", "") or "")
    if not run_id:
        raise HTTPException(status_code=409, detail="任务运行 ID 缺失")
    try:
        await client.runs.cancel(task_id, run_id, wait=False)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="终止异步任务失败，请稍后重试",
        ) from exc
    return {"ok": True, "task_id": task_id, "run_id": run_id, "status": "cancelling"}


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
    status = _status_from_run(latest_run)
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
    if delivered:
        try:
            await agent_loader.mark_async_task_delivered(
                task_id,
                user_id=binding.user_id,
                thread_id=binding.thread_id,
                status=status,
            )
        except Exception:
            logger.warning("异步任务投递标记写入失败：%s", task_id, exc_info=True)

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
