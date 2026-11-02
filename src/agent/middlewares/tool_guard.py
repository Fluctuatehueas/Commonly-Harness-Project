"""子 Agent 工具级权限守卫：在工具层拒绝写类与执行类工具。

为什么不用 ``FilesystemPermission``：deepagents 的路径级权限只作用于
内置文件工具，``execute`` 是 shell，``cat`` / ``rm`` / 重定向都能绕过
任何路径规则，因此框架对「支持执行的 backend + permissions」组合直接
拒绝构图。这里把「只读」落到工具调用层：既从模型可见工具里移除，也在
执行前拒绝，不依赖 shell 自觉。
"""

from __future__ import annotations

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


def _tool_name(tool: Any) -> str | None:
    """兼容 ``BaseTool`` 与 ``{"name": ...}`` 两种工具声明形式。"""
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class DeniedToolsMiddleware(AgentMiddleware):
    """让指定工具对模型不可见，并在被调用时返回权限拒绝结果。"""

    def __init__(
        self,
        denied: list[str] | tuple[str, ...],
        *,
        reason: str = "该子 Agent 为只读角色，禁止调用此工具。",
    ) -> None:
        super().__init__()
        self._denied = frozenset(str(name) for name in denied)
        self._reason = reason

    # ---------- 模型可见工具 ----------
    # 先隐藏再拒绝：隐藏避免模型无意义地重试，拒绝是最后的硬边界。

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """同步：从本次模型请求的工具列表中剔除被禁工具。"""
        filtered = [tool for tool in request.tools if _tool_name(tool) not in self._denied]
        return handler(request.override(tools=filtered))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> Any:
        """异步：与同步版本等价，DeepAgents 主链路走这里。"""
        filtered = [tool for tool in request.tools if _tool_name(tool) not in self._denied]
        return await handler(request.override(tools=filtered))

    # ---------- 工具调用守卫 ----------

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        """命中被禁工具时直接返回错误结果，不进入真实工具。"""
        name = str(request.tool_call.get("name", ""))
        if name not in self._denied:
            return None
        return ToolMessage(
            content=f"Error: {name} 已被拒绝。{self._reason}",
            tool_call_id=request.tool_call.get("id") or "",
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步工具调用拦截。"""
        return self._rejection(request) or handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步工具调用拦截。"""
        return self._rejection(request) or await handler(request)
