"""团队工具可见性与硬拒绝。

声明式子 Agent 与父图共享同一份 runtime context，因此这里不使用
``runtime.context.agent_id`` 判身份：身份在实例化中间件时固化。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

TEAM_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "list_my_groups",
        "get_group_overview",
        "discover_agent_groups",
        "delegate_to_group_leader",
    }
)


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


class TeamPermissionMiddleware(AgentMiddleware):
    """让团队工具对无权限的 Agent 不可见，并在被调用时返回拒绝结果。"""

    def __init__(
        self,
        *,
        agent_id: str,
        group_id: str,
        allowed: frozenset[str],
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._group_id = group_id
        self._allowed = frozenset(allowed)

    def _denied(self, name: str) -> bool:
        return name in TEAM_TOOL_NAMES and name not in self._allowed

    def _filter(self, tools: list[Any]) -> list[Any]:
        return [tool for tool in tools if not self._denied(_tool_name(tool))]

    # ---------- 模型可见工具 ----------

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        return handler(request.override(tools=self._filter(list(request.tools))))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> Any:
        return await handler(request.override(tools=self._filter(list(request.tools))))

    # ---------- 工具调用守卫 ----------

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name", ""))
        if not self._denied(name):
            return None
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error": "permission_denied",
                    "detail": (
                        f"{name} 需要组长权限；"
                        f"当前 Agent={self._agent_id} group={self._group_id}"
                    ),
                },
                ensure_ascii=False,
            ),
            tool_call_id=request.tool_call.get("id") or "",
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return self._rejection(request) or handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return self._rejection(request) or await handler(request)
