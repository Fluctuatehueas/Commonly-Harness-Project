"""子 Agent 进度回调中间件。

主 Agent 通过 ``task`` 工具并发委派多个子 Agent 时，每个子 Agent 的内部
过程（模型输出、工具调用）虽然会随子图流出来，但缺少一个稳定的"实例标识"：
同一类型的多个实例混在一起，前端无法判断某条进度属于哪一个任务。

本中间件为每次子 Agent 运行生成唯一 ``run_id``，并把阶段事件通过
LangGraph 的 custom stream 通道推给主图：

    {"type": "subagent_progress", "subagent": "...", "run_id": "...",
     "event": "started | tool_start | tool_end | finished",
     "tool": "...", "message": "..."}

``run_id`` 在单次委派内稳定，因此并发实例之间不会串进度。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_RUN_ID: ContextVar[str] = ContextVar("subagent_progress_run_id", default="")
_PREVIEW_LIMIT = 200


def _preview(value: Any, limit: int = _PREVIEW_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(text or "").split())
    return f"{text[:limit]}…" if len(text) > limit else text


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    return str(content or "").strip()


def _last_message(state: dict[str, Any], role: str) -> Any | None:
    for message in reversed(list((state or {}).get("messages", []) or [])):
        if getattr(message, "type", None) == role:
            return message
    return None


class SubagentProgressMiddleware(AgentMiddleware):
    """把子 Agent 的阶段与工具调用作为 custom 事件上报。"""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._subagent_name = name

    def _emit(self, payload: dict[str, Any]) -> None:
        try:
            writer = get_stream_writer()
            writer({"type": "subagent_progress", **payload})
        except Exception:
            # 没有 custom 流（例如单测直接调用子图）时静默跳过。
            pass

    def _run_id(self) -> str:
        run_id = _RUN_ID.get()
        if not run_id:
            run_id = uuid4().hex[:12]
            _RUN_ID.set(run_id)
        return run_id

    def _event(self, event: str, **extra: Any) -> dict[str, Any]:
        return {
            "subagent": self._subagent_name,
            "run_id": self._run_id(),
            "event": event,
            **extra,
        }

    # ---------- 生命周期 ----------

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> None:
        _RUN_ID.set(uuid4().hex[:12])
        task = _last_message(state, "human")
        self._emit(
            self._event(
                "started",
                message=_preview(_message_text(task) if task is not None else ""),
            )
        )
        return None

    async def aafter_agent(self, state: dict[str, Any], runtime: Any) -> None:
        answer = _last_message(state, "ai")
        self._emit(
            self._event(
                "finished",
                message=_preview(_message_text(answer) if answer is not None else ""),
            )
        )
        _RUN_ID.set("")
        return None

    # ---------- 工具调用 ----------

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = str(request.tool_call.get("name", ""))
        self._emit(
            self._event(
                "tool_start",
                tool=tool_name,
                message=_preview(request.tool_call.get("args")),
            )
        )
        try:
            result = await handler(request)
        except Exception as exc:
            self._emit(self._event("tool_end", tool=tool_name, status="error", message=str(exc)))
            raise
        status = getattr(result, "status", None) or "done"
        self._emit(
            self._event(
                "tool_end",
                tool=tool_name,
                status="error" if status == "error" else "done",
                message=_preview(getattr(result, "content", "")),
            )
        )
        return result


__all__ = ["SubagentProgressMiddleware"]
