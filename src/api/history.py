"""会话历史 HTTP 接口。

会话正文始终从 LangGraph 的 PostgreSQL checkpoint 恢复；
会话索引保存在 PostgreSQL Store 的 ``("sessions", user_id)`` 命名空间。
后续接入登录后只需传入真实 ``user_id``，无需改变消息存储结构。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from agent import project_store
from agent.mcp.runtime import mcp_runtime
from agent.mcp.types import ServerState
from agent.schema import (
    DeleteSessionResponse,
    Message,
    Session,
    SessionListResponse,
    SessionMessagesResponse,
    SessionUpdateRequest,
)
from agent.tools.async_sandbox_tools import START_ASYNC_TASK_TOOL_NAME
from api.agent_loader import agent_loader
from api.async_tasks import extract_async_task_id
from api.message_utils import content_to_text, serialize_interrupt


router = APIRouter(prefix="/history", tags=["history"])
DEFAULT_SESSION_TITLE = "新会话"
# 会话在多少天没有新记录后自动归档；0 表示关闭自动归档。
ENV_KEY_ARCHIVE_DAYS = "HARNESS_SESSION_ARCHIVE_DAYS"
DEFAULT_ARCHIVE_DAYS = 30


def _user_display_content(content: Any) -> str:
    """用户消息只回显原始文本，不把注入的附件正文带进历史列表。

    有附件时 checkpoint 里存的是多模态块列表：第 1 块是用户原话，
    后续块是服务端注入的文档文本和元信息。
    """
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return content_to_text(content)


def _message_value(message: Any, name: str, default: Any = None) -> Any:
    """从 LangChain 消息对象或 checkpoint 字典中读取指定字段。"""
    # checkpoint 迁移或不同 LangGraph 版本可能产生对象或字典，两种格式必须兼容。
    return (
        message.get(name, default)
        if isinstance(message, dict)
        else getattr(message, name, default)
    )


def _message_role(message: Any) -> str:
    """将 LangChain 的消息类型标准化为前端使用的角色名称。"""
    # LangChain 使用 human/ai，前端和 schema 统一使用 user/assistant。
    role = _message_value(message, "role") or _message_value(message, "type")
    return {"human": "user", "ai": "assistant"}.get(str(role), str(role or "assistant"))


def _task_subagent_name(args: Any) -> str | None:
    """从 task 工具参数中读取隔离子 Agent 的原名。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    name = args.get("subagent_type")
    return str(name) if name else None


def _task_description(args: Any) -> str:
    """从委派参数中提取给用户展示的任务摘要，而不暴露完整参数。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return ""
    if not isinstance(args, dict):
        return ""
    description = args.get("description")
    return str(description).strip() if description else ""


def _message_reasoning(message: Any) -> str:
    """从 checkpoint 的 AI 消息中读取思考内容，供历史轨迹树回放。"""
    additional = _message_value(message, "additional_kwargs", {}) or {}
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if value:
                return str(value)
    content = _message_value(message, "content", None)
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
        )
    return ""


def serialize_messages(
    messages: list[Any],
    usage_by_message: dict[str, dict[str, int]] | None = None,
) -> list[Message]:
    """将 checkpoint 消息转换为与 SSE 流式过程一致的前端展示消息。

    助手消息中的工具调用先转为“执行中”占位消息；随后到达的工具结果按
    ``tool_call_id`` 回填该占位消息，避免历史记录和实时展示的顺序不一致。
    ``usage_by_message`` 以最终回答消息 ID 为键提供单轮 Token 消耗。
    """
    serialized: list[Message] = []
    tool_messages: dict[str, Message] = {}
    now = datetime.now(timezone.utc)
    completed_tasks = {
        str(_message_value(message, "id", "")).removeprefix("async-task-result:")
        for message in messages
        if str(_message_value(message, "id", "")).startswith("async-task-result:")
    }

    for index, message in enumerate(messages):
        role = _message_role(message)
        message_content = _message_value(message, "content", "")
        # 用户消息带附件时，只回显原话，避免把注入的文档正文带进历史列表。
        content = (
            _user_display_content(message_content)
            if role == "user"
            else content_to_text(message_content)
        )
        if role == "assistant":
            # AIMessage 可同时携带文本和工具调用，必须先保留文本的原始顺序。
            additional_kwargs = _message_value(message, "additional_kwargs", {}) or {}
            source = str(additional_kwargs.get("source", "main"))
            # 思考内容先于同一条 AI 消息的正文与工具调用到达，历史也按同样顺序回放。
            reasoning = _message_reasoning(message)
            if reasoning:
                serialized.append(
                    Message(
                        id=f"reasoning-{index}",
                        role="reasoning",
                        content=reasoning,
                        source=source,
                        created_at=now,
                    )
                )
            if content:
                # 子 Agent 自身的文本是内部执行记录，用户历史只展示主 Agent 交付。
                if source != "main":
                    continue
                serialized.append(
                    Message(
                        id=str(_message_value(message, "id", None) or f"message-{index}"),
                        role="assistant",
                        content=content,
                        source=source,
                        async_task_id=additional_kwargs.get("async_task_id"),
                        created_at=now,
                        tokens=(
                            usage_by_message.get(
                                str(_message_value(message, "id", "") or "")
                            )
                            if usage_by_message
                            else None
                        ),
                    )
                )
            for call_index, tool_call in enumerate(
                _message_value(message, "tool_calls", []) or []
            ):
                call = tool_call if isinstance(tool_call, dict) else {}
                call_id = str(call.get("id") or f"tool-{index}-{call_index}")
                tool_name = str(call.get("name", "tool"))
                is_delegation = tool_name in {"task", START_ASYNC_TASK_TOOL_NAME}
                subagent_name = _task_subagent_name(call.get("args")) if is_delegation else None
                # task 是主 Agent 与隔离子 Agent 的边界。
                # 历史中将其保留为委派记录，而不是伪装成普通工具调用。
                tool_message = Message(
                    id=call_id,
                    role="delegation" if is_delegation else "tool",
                    tool_name=tool_name,
                    # 子任务仅暴露面向用户的任务摘要；普通工具仍保留实际参数。
                    content=_task_description(call.get("args")) if is_delegation else "",
                    args="" if is_delegation else str(call.get("args", "")),
                    text="",
                    tool_status="calling",
                    source=subagent_name or ("subagent" if is_delegation else "main"),
                    created_at=now,
                )
                serialized.append(tool_message)
                tool_messages[call_id] = tool_message
        elif role == "tool":
            # 工具结果通常带 tool_call_id；缺失时仍展示为独立的工具结果。
            call_id = str(_message_value(message, "tool_call_id", ""))
            tool_message = tool_messages.get(call_id)
            tool_status = "error" if _message_value(message, "status") == "error" else "done"
            if tool_message is None:
                serialized.append(
                    Message(
                        id=call_id or f"message-{index}",
                        role="tool",
                        tool_name=str(_message_value(message, "name", "tool")),
                        text=content,
                        tool_status=tool_status,
                        source="main",
                        created_at=now,
                    )
                )
            else:
                if (
                    tool_message.role == "delegation"
                    and tool_message.tool_name == START_ASYNC_TASK_TOOL_NAME
                    and tool_status != "error"
                ):
                    # 保留远程 ID，使刷新后的前端可以继续轮询尚未投递的结果。
                    task_id = extract_async_task_id(message_content)
                    tool_message.async_task_id = task_id
                    if task_id in completed_tasks:
                        tool_message.tool_status = "done"
                    continue
                tool_message.tool_status = tool_status
                if tool_status == "error":
                    tool_message.text = content
                if tool_message.role == "delegation" and tool_message.tool_name == "task":
                    # 同步子 Agent 只回传最终报告；异步任务的最终交付由
                    # 主会话中的 source=main 消息承载。
                    tool_message.text = content
                elif tool_message.role != "delegation":
                    tool_message.text = content
        elif role == "user":
            serialized.append(
                Message(
                    id=f"message-{index}",
                    role="user",
                    content=content,
                    created_at=now,
                )
            )
    return serialized


def _session_from_index(item: dict[str, Any], message_count: int) -> Session:
    """将 PostgreSQL Store 中的会话索引转换为接口响应模型。"""
    return Session(
        thread_id=item["thread_id"],
        title=item.get("title") or DEFAULT_SESSION_TITLE,
        created_at=datetime.fromisoformat(item["created_at"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
        message_count=message_count,
        pinned=bool(item.get("pinned", False)),
        project_id=item.get("project_id") or None,
        integration_server_id=item.get("integration_server_id") or None,
        archived=bool(item.get("archived", False)),
        archived_at=(
            datetime.fromisoformat(item["archived_at"])
            if item.get("archived_at")
            else None
        ),
    )


@router.post("", response_model=Session)
async def create_session(
    user_id: str = Query("anonymous", min_length=1),
    project_id: str | None = Query(None, min_length=1),
    integration_server_id: str | None = Query(None, min_length=1),
) -> Session:
    """创建一条尚未发送消息的会话。

    传入 ``project_id`` 时创建项目会话；传入 ``integration_server_id`` 时
    创建绑定用户自有后端 MCP 的集成会话。两者互斥，归属在创建时固定。
    """
    if project_id is not None and integration_server_id is not None:
        raise HTTPException(
            status_code=422, detail="项目会话与集成后端会话不能同时创建"
        )
    if project_id is not None:
        project = await asyncio.to_thread(
            project_store.get_project, user_id, project_id
        )
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        if project["status"] == "unavailable":
            raise HTTPException(status_code=422, detail="项目目录不可用")
    if integration_server_id is not None:
        config = mcp_runtime.config_for(integration_server_id)
        if config is None or not config.integration:
            raise HTTPException(status_code=404, detail="集成项目不存在")
        if not config.enabled:
            raise HTTPException(status_code=422, detail="集成项目已停用")
        runtime = mcp_runtime.runtime_for(integration_server_id)
        if runtime is None or runtime.state not in {
            ServerState.CONNECTED,
            ServerState.DEGRADED,
        }:
            raise HTTPException(
                status_code=409,
                detail="集成项目未连接，请先在 MCP 服务页测试连接",
            )
    thread_id = str(uuid4())
    await agent_loader.save_session(
        user_id,
        thread_id,
        DEFAULT_SESSION_TITLE,
        project_id=project_id,
        bind_project=True,
        integration_server_id=integration_server_id,
        bind_integration=True,
    )
    session = await agent_loader.get_session(user_id, thread_id)
    assert session is not None
    return _session_from_index(session, message_count=0)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Query("anonymous", min_length=1),
    archived: bool = Query(False, description="true=返回已归档会话，false=返回正常会话"),
) -> SessionListResponse:
    """获取当前用户已登记的会话列表；``archived=true`` 时只返回已归档会话。"""
    indexed_sessions = await agent_loader.list_sessions(user_id, archived=archived)

    # 索引只保存列表元数据；消息数由 DeepAgents 重建的框架状态决定。
    # 空会话也必须返回，否则首次打开时刚创建的“新会话”会被错误隐藏。
    sessions: list[Session] = []
    for item in indexed_sessions:
        messages = await agent_loader.get_thread_messages(item["thread_id"])
        sessions.append(
            _session_from_index(item, message_count=len(serialize_messages(messages)))
        )
    return SessionListResponse(sessions=sessions)


def _archive_days() -> int:
    """读取自动归档天数；未配置或非法时回落到 30 天，0 表示关闭。"""
    raw = os.getenv(ENV_KEY_ARCHIVE_DAYS, "").strip()
    try:
        days = int(raw) if raw else DEFAULT_ARCHIVE_DAYS
    except ValueError:
        days = DEFAULT_ARCHIVE_DAYS
    return max(days, 0)


@router.post("/auto-archive")
async def auto_archive_sessions(
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """按配置天数归档长期无新记录的会话。

    前端只在进入对话页时调用一次，不在后台常驻计时器。
    """
    days = _archive_days()
    archived = await agent_loader.archive_stale_sessions(user_id, days)
    return {"archived": archived, "days": days}


@router.get("/{thread_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(
    thread_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> SessionMessagesResponse:
    """恢复当前用户指定会话的全部可展示消息；已归档会话允许只读查看。"""
    # 先检查会话索引，避免仅凭 thread_id 读取到其他用户的 checkpoint。
    await agent_loader.require_session(user_id, thread_id, allow_archived=True)
    state = await agent_loader.get_thread_state(thread_id)
    interrupts = state.interrupts
    # 单轮消耗按最终回答消息 ID 回填；累计值直接透传给发送栏展示。
    usage_data = await agent_loader.get_thread_usage(user_id, thread_id)
    usage_by_message = {
        str(entry.get("message_id", "")): {
            "input": int(entry.get("input") or 0),
            "output": int(entry.get("output") or 0),
            "total": int(entry.get("total") or 0),
        }
        for entry in usage_data.get("rounds", [])
        if entry.get("message_id")
    }
    return SessionMessagesResponse(
        thread_id=thread_id,
        messages=serialize_messages(
            list(state.values.get("messages", [])), usage_by_message
        ),
        interrupt=(
            serialize_interrupt(interrupts[0], thread_id) if interrupts else None
        ),
        session_usage=usage_data.get("session", {}),
    )


@router.patch("/{thread_id}", response_model=Session)
async def update_session(
    thread_id: str,
    body: SessionUpdateRequest,
    user_id: str = Query("anonymous", min_length=1),
) -> Session:
    """重命名会话或切换置顶；项目归属在创建时固定，不允许修改。"""
    if await agent_loader.get_session(user_id, thread_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    if "project_id" in body.model_fields_set:
        raise HTTPException(
            status_code=422, detail="会话归属项目在创建时固定，不能修改"
        )
    title = body.title.strip() if isinstance(body.title, str) else None
    if title is not None and not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    # 归档 / 恢复属于管理操作，需要显式放行已归档会话。
    session = await agent_loader.get_session(user_id, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    if session.get("archived") and body.archived is None:
        raise HTTPException(
            status_code=409,
            detail="会话已归档，请先恢复后再修改",
        )
    updated = await agent_loader.update_session(
        user_id,
        thread_id,
        title=title,
        pinned=body.pinned,
        archived=body.archived,
    )
    assert updated is not None
    messages = await agent_loader.get_thread_messages(thread_id)
    return _session_from_index(updated, message_count=len(serialize_messages(messages)))


@router.delete("/{thread_id}", response_model=DeleteSessionResponse)
async def delete_session(
    thread_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> DeleteSessionResponse:
    """删除当前用户的一条会话及其 checkpoint 数据（含已归档会话）。"""
    # 与读取接口使用同一所有权校验，防止跨用户删除已知的 thread_id。
    await agent_loader.require_session(user_id, thread_id, allow_archived=True)
    await agent_loader.delete_session(user_id, thread_id)
    return DeleteSessionResponse(success=True)


# 前端会话设置的新契约路径：PATCH /sessions/{thread_id}。
# 复用 /history 下的同一实现，避免两套更新逻辑。
sessions_router = APIRouter(tags=["history"])


@sessions_router.patch("/sessions/{thread_id}", response_model=Session, include_in_schema=False)
async def update_session_by_sessions_path(
    thread_id: str,
    body: SessionUpdateRequest,
    user_id: str = Query("anonymous", min_length=1),
) -> Session:
    """PATCH /sessions/{thread_id} 别名，行为与 /history/{thread_id} 一致。"""
    return await update_session(thread_id=thread_id, body=body, user_id=user_id)
