"""插件 hook 与提示词注入中间件。

放在中间件链最内层（``TimeAwarenessMiddleware`` 之前）：
- before_model 在其它中间件处理完之后、模型调用之前执行；
- after_model 最先拿到模型输出；
- before_tool / after_tool 包裹真实工具调用。

所有插件 hook 都是「可降级」的：超时、崩溃或返回非法结构都不会阻断主流程。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _text_of(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _last_human_text(messages: list[Any]) -> str:
    for message in reversed(messages or []):
        if getattr(message, "type", "") in {"human", "user"}:
            return _text_of(message)[:2000]
    return ""


class PluginHookMiddleware(AgentMiddleware):
    """把插件 hook 与提示词接入 Agent 运行链路。"""

    name = "plugin_hooks"

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def _runtime() -> Any:
        from agent.identity import current_user_id
        from plugin_bus.runtime import get_plugin_runtime

        return get_plugin_runtime(current_user_id())

    async def _prompt_section(self, request: ModelRequest) -> str:
        try:
            return await self._runtime().prompt_section(
                _last_human_text(list(request.messages or []))
            )
        except Exception:
            logger.debug("插件提示词注入失败", exc_info=True)
            return ""

    def _apply_prompt(self, request: ModelRequest, section: str) -> ModelRequest:
        if not section:
            return request
        existing = request.system_message
        text = _text_of(existing) if existing is not None else ""
        merged = f"{text}\n\n{section}".strip() if text else section
        return request.override(system_message=SystemMessage(content=merged))

    async def _before_model(self, request: ModelRequest) -> None:
        payload = {
            "messages": [
                {
                    "role": getattr(message, "type", ""),
                    "content": _text_of(message)[:1000],
                }
                for message in list(request.messages or [])[-10:]
            ],
            "system_prompt": _text_of(request.system_message)[:2000]
            if request.system_message is not None
            else "",
        }
        try:
            await self._runtime().invoke_hooks("before_model", payload)
        except Exception:
            logger.debug("before_model hook 调用失败", exc_info=True)

    async def _after_model(self, response: ModelResponse) -> None:
        payload = {
            "messages": [
                {
                    "role": getattr(message, "type", ""),
                    "content": _text_of(message)[:1000],
                }
                for message in list(response.result or [])[-5:]
            ]
        }
        try:
            await self._runtime().invoke_hooks("after_model", payload)
        except Exception:
            logger.debug("after_model hook 调用失败", exc_info=True)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        # 同步链路不注入插件能力；DeepAgents 主链路走 async 版本。
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> Any:
        section = await self._prompt_section(request)
        prepared = self._apply_prompt(request, section)
        await self._before_model(prepared)
        response = await handler(prepared)
        await self._after_model(response)
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_call = request.tool_call or {}
        name = str(tool_call.get("name") or "")
        if not name.startswith("plugin__"):
            return await handler(request)
        arguments = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        runtime = self._runtime()
        try:
            decisions = await runtime.invoke_hooks(
                "before_tool", {"tool": name, "arguments": arguments}
            )
        except Exception:
            decisions = []
        for decision in decisions:
            result = decision.get("result")
            if isinstance(result, dict) and result.get("allow") is False:
                reason = str(result.get("reason") or "插件 hook 拒绝了该工具调用")
                return ToolMessage(
                    content=f"Error: {reason}",
                    tool_call_id=tool_call.get("id") or "",
                    name=name,
                    status="error",
                )
        output = await handler(request)
        try:
            text = _text_of(output) if not isinstance(output, Command) else ""
            patches = await runtime.invoke_hooks(
                "after_tool",
                {"tool": name, "arguments": arguments, "result": text[:2000]},
            )
        except Exception:
            patches = []
        for patch in patches:
            result = patch.get("result")
            if (
                isinstance(result, dict)
                and isinstance(result.get("content"), str)
                and isinstance(output, ToolMessage)
            ):
                output = ToolMessage(
                    content=result["content"],
                    tool_call_id=output.tool_call_id,
                    name=output.name,
                    status=output.status,
                )
        return output


__all__ = ["PluginHookMiddleware"]
