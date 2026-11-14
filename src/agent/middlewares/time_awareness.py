"""时间属性中间件。

只保留「消息自带时间属性」，不再向模型请求注入对话时间线：

- 模型返回后：给 AI 消息补写时间戳随 checkpoint 持久化，并清洗掉正文里
  可能被模型复述的时间标记；
- 工具返回后：给工具结果补写时间戳，后续轮次同样能回放；
- 子 Agent 入口消息缺少时间戳时，按首次出现时间补一个。

时间戳只写 ``additional_kwargs``，不会透传给模型服务。需要知道当前时间时，
由模型主动调用 ``get_current_time`` 工具——这样请求前缀保持稳定，不会因为
秒级时间或相对时间变化导致上下文缓存整体失效。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.time_context import (
    message_timestamp,
    now_iso,
    stamp_message,
    strip_time_tags,
)


def _sanitize_message(message: Any) -> Any:
    """去掉正文里被复述的时间标记；只处理文本，不动工具调用等结构。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        cleaned = strip_time_tags(content)
        if cleaned == content:
            return message
        return message.model_copy(update={"content": cleaned})
    if isinstance(content, list):
        changed = False
        blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                cleaned = strip_time_tags(text)
                if cleaned != text:
                    changed = True
                    blocks.append({**block, "text": cleaned})
                    continue
            blocks.append(block)
        if changed:
            return message.model_copy(update={"content": blocks})
    return message


def _stamp_and_sanitize(message: Any) -> Any:
    return _sanitize_message(stamp_message(message))


class TimeAwarenessMiddleware(AgentMiddleware):
    """给消息补时间戳，并在模型调用时附上一段对话时间线。"""

    name = "time_awareness"

    def __init__(self, *, stamp_missing_human: bool = False) -> None:
        """
        Args:
            stamp_missing_human: 入口消息（如子 Agent 收到的委派任务）没有
                时间戳时，按首次出现的时间补一个。主 Agent 的用户消息由 API
                层写入真实发送时间，保持关闭，避免给历史消息伪造时间。
        """
        super().__init__()
        self._stamp_missing_human = stamp_missing_human
        # 无 ID 的入口消息用内容指纹缓存首次出现时间，保证同一轮多次模型调用
        # 看到的时间一致。
        self._fallback_times: dict[str, str] = {}

    def _fallback_key(self, message: Any) -> str:
        message_id = str(getattr(message, "id", "") or "")
        if message_id:
            return message_id
        return f"{getattr(message, 'type', '')}:{hash(str(getattr(message, 'content', '')))}"

    def _fill_entry_times(self, messages: list[Any]) -> list[Any]:
        """给没有时间戳的入口消息补时间（仅子 Agent 开启）。"""
        if not self._stamp_missing_human:
            return messages
        filled: list[Any] = []
        for message in messages:
            if getattr(message, "type", "") in {"human", "user"} and not message_timestamp(message):
                key = self._fallback_key(message)
                value = self._fallback_times.setdefault(key, now_iso())
                message = stamp_message(message, value)
            filled.append(message)
        return filled

    def _prepare(self, request: ModelRequest) -> ModelRequest:
        """只补入口消息的时间属性，不改写任何消息正文。

        时间线会随秒级时间和相对时间变化，一旦写进历史消息中部，其后的全部
        上下文都无法命中前缀缓存；时间感知改由 ``get_current_time`` 工具承担。
        """
        messages = self._fill_entry_times(list(request.messages or []))
        return request.override(messages=messages)

    @staticmethod
    def _finalize_response(response: ModelResponse) -> ModelResponse:
        result = [_stamp_and_sanitize(message) for message in response.result]
        return ModelResponse(
            result=result,
            structured_response=response.structured_response,
        )

    @staticmethod
    def _stamp_tool_result(result: Any) -> Any:
        """工具结果可能是 ToolMessage，也可能是带消息的 Command。"""
        if isinstance(result, Command):
            update = getattr(result, "update", None)
            if isinstance(update, dict) and isinstance(update.get("messages"), list):
                result.update["messages"] = [
                    _stamp_and_sanitize(message) for message in update["messages"]
                ]
            return result
        if getattr(result, "type", "") == "tool":
            return _stamp_and_sanitize(result)
        return result

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._finalize_response(handler(self._prepare(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return self._finalize_response(await handler(self._prepare(request)))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        return self._stamp_tool_result(handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        return self._stamp_tool_result(await handler(request))


__all__ = ["TimeAwarenessMiddleware"]
