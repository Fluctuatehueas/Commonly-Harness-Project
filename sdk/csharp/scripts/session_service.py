"""会话管理 gRPC 服务：列表、历史、重命名、置顶、删除、创建。

直接复用 ``api.history.serialize_messages`` 与 ``AgentLoader`` 的会话索引，
保证 gRPC 与 HTTP 两条链路看到完全一致的历史数据。
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402

import Agent_pb2  # noqa: E402
import Session_pb2  # noqa: E402
import Session_pb2_grpc  # noqa: E402

from api.agent_loader import AgentLoader  # noqa: E402
from api.history import serialize_messages  # noqa: E402
from api.message_utils import serialize_interrupt  # noqa: E402


DEFAULT_SESSION_TITLE = "新会话"
LAST_MESSAGE_PREVIEW_CHARS = 120


def _user_id(request: Any) -> str:
    return request.user_id.strip() if request.user_id and request.user_id.strip() else "anonymous"


def _usage_by_message(usage_data: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        str(entry.get("message_id", "")): {
            "input": int(entry.get("input") or 0),
            "output": int(entry.get("output") or 0),
            "total": int(entry.get("total") or 0),
        }
        for entry in usage_data.get("rounds", [])
        if entry.get("message_id")
    }


async def _load_serialized(
    loader: AgentLoader,
    user_id: str,
    thread_id: str,
) -> tuple[list[Any], Any, dict[str, Any]]:
    """从 checkpoint 恢复展示消息，并附带单轮 Token 统计。"""
    state = await loader.get_thread_state(thread_id)
    usage_data = await loader.get_thread_usage(user_id, thread_id)
    messages = serialize_messages(
        list(state.values.get("messages", [])),
        _usage_by_message(usage_data),
    )
    return messages, state, usage_data


def _to_chat_message(message: Any) -> Agent_pb2.ChatMessage:
    """把前端展示消息转换为 LangChain 风格 ChatMessage。"""
    role = {
        "user": Agent_pb2.MESSAGE_ROLE_HUMAN,
        "assistant": Agent_pb2.MESSAGE_ROLE_AI,
        "tool": Agent_pb2.MESSAGE_ROLE_TOOL,
        "delegation": Agent_pb2.MESSAGE_ROLE_TOOL,
    }.get(str(message.role), Agent_pb2.MESSAGE_ROLE_UNSPECIFIED)

    extra = {
        "source": message.source,
        "tool_status": message.tool_status,
        "args": message.args,
        "async_task_id": message.async_task_id,
        "tokens": message.tokens,
    }
    extra = {key: value for key, value in extra.items() if value is not None}

    return Agent_pb2.ChatMessage(
        message_id=str(message.id),
        role=role,
        content=str(message.content or message.text or ""),
        tool_call_id=str(message.tool_call_id or ""),
        name=str(message.tool_name or message.source or ""),
        additional_kwargs_json=json.dumps(extra, ensure_ascii=False),
        created_at=(
            message.created_at.isoformat()
            if getattr(message, "created_at", None) is not None
            else ""
        ),
    )


def _preview(messages: list[Any]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    text = str(last.content or last.text or "").strip()
    return text[:LAST_MESSAGE_PREVIEW_CHARS]


def _build_session_info(
    item: dict[str, Any],
    all_messages: list[Any],
    selected_messages: list[Any] | None = None,
    usage_data: dict[str, Any] | None = None,
) -> Session_pb2.SessionInfo:
    usage = (usage_data or {}).get("session", {})
    return Session_pb2.SessionInfo(
        thread_id=str(item["thread_id"]),
        title=str(item.get("title") or DEFAULT_SESSION_TITLE),
        created_at=str(item.get("created_at", "")),
        updated_at=str(item.get("updated_at", "")),
        message_count=len(all_messages),
        pinned=bool(item.get("pinned", False)),
        last_message_preview=_preview(all_messages),
        usage_json=json.dumps(usage, ensure_ascii=False),
        messages=[_to_chat_message(message) for message in (selected_messages or [])],
    )


class SessionServiceServicer(Session_pb2_grpc.SessionServiceServicer):
    """会话索引与历史记录服务。"""

    def __init__(self, loader: AgentLoader) -> None:
        self._loader = loader

    async def ListSessions(
        self,
        request: Session_pb2.ListSessionsRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.ListSessionsResponse:
        user_id = _user_id(request)
        indexed = await self._loader.list_sessions(user_id)
        response = Session_pb2.ListSessionsResponse()

        for item in indexed:
            thread_id = str(item["thread_id"])
            messages, _, usage_data = await _load_serialized(
                self._loader,
                user_id,
                thread_id,
            )
            selected = messages
            if request.messages_limit_per_session > 0:
                selected = messages[-request.messages_limit_per_session:]

            info = _build_session_info(
                item,
                messages,
                selected_messages=selected if request.include_messages else [],
                usage_data=usage_data if request.include_usage else None,
            )
            if not request.include_usage:
                info.usage_json = ""
            response.sessions.append(info)

        return response

    async def GetSessionHistory(
        self,
        request: Session_pb2.GetSessionHistoryRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.GetSessionHistoryResponse:
        user_id = _user_id(request)
        item = await self._loader.get_session(user_id, request.thread_id)
        if item is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        messages, state, usage_data = await _load_serialized(
            self._loader,
            user_id,
            request.thread_id,
        )
        selected = list(messages)

        if request.before_message_id:
            index = next(
                (
                    position
                    for position, message in enumerate(selected)
                    if str(message.id) == request.before_message_id
                ),
                None,
            )
            if index is not None:
                selected = selected[:index]

        if request.limit > 0:
            selected = selected[-request.limit:]

        interrupt_json = ""
        if state.interrupts:
            interrupt_json = json.dumps(
                serialize_interrupt(state.interrupts[0], request.thread_id),
                ensure_ascii=False,
            )

        return Session_pb2.GetSessionHistoryResponse(
            session=_build_session_info(
                item,
                messages,
                selected_messages=selected,
                usage_data=usage_data,
            ),
            messages=[_to_chat_message(message) for message in selected],
            interrupt_json=interrupt_json,
            session_usage_json=json.dumps(
                usage_data.get("session", {}),
                ensure_ascii=False,
            ),
        )

    async def CreateSession(
        self,
        request: Session_pb2.CreateSessionRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.SessionInfo:
        user_id = _user_id(request)
        thread_id = str(uuid.uuid4())
        title = request.title.strip() or DEFAULT_SESSION_TITLE
        await self._loader.save_session(user_id, thread_id, title)
        item = await self._loader.get_session(user_id, thread_id)
        if item is None:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                "创建会话失败",
            )
        return _build_session_info(item, [], [])

    async def RenameSession(
        self,
        request: Session_pb2.RenameSessionRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.SessionInfo:
        user_id = _user_id(request)
        item = await self._loader.get_session(user_id, request.thread_id)
        if item is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        title = request.title.strip()
        if not title:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "标题不能为空",
            )

        updated = await self._loader.update_session(
            user_id,
            request.thread_id,
            title=title,
        )
        if updated is None:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                "重命名会话失败",
            )

        messages, _, usage_data = await _load_serialized(
            self._loader,
            user_id,
            request.thread_id,
        )
        return _build_session_info(updated, messages, [], usage_data)

    async def SetSessionPinned(
        self,
        request: Session_pb2.SetSessionPinnedRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.SessionInfo:
        user_id = _user_id(request)
        item = await self._loader.get_session(user_id, request.thread_id)
        if item is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        updated = await self._loader.update_session(
            user_id,
            request.thread_id,
            pinned=request.pinned,
        )
        if updated is None:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                "更新置顶状态失败",
            )

        messages, _, usage_data = await _load_serialized(
            self._loader,
            user_id,
            request.thread_id,
        )
        return _build_session_info(updated, messages, [], usage_data)

    async def DeleteSession(
        self,
        request: Session_pb2.DeleteSessionRequest,
        context: grpc.aio.ServicerContext,
    ) -> Session_pb2.DeleteSessionResponse:
        user_id = _user_id(request)
        item = await self._loader.get_session(user_id, request.thread_id)
        if item is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        success = await self._loader.delete_session(user_id, request.thread_id)
        return Session_pb2.DeleteSessionResponse(success=bool(success))
