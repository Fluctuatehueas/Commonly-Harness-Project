"""对话内容与中断协议的统一转换层。

只保留与业务无关的通用能力：
- ``content_to_text``：LangChain / MCP 内容降级为纯文本。
- ``serialize_interrupt``：LangGraph 中断对象统一成前端协议。
- ``make_session_title``：从首条用户消息生成稳定标题。

业务侧如需识别图片、图表或自定义资源块，可在接入方自行注册扩展，
不要把业务类型写进本模块。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable


# ============================================================
# 文本渲染
# ============================================================


def _parse_json_content(content: Any) -> Any:
    """幂等：非字符串 / 非 JSON 原样返回。"""
    if not isinstance(content, str):
        return content
    stripped = content.strip()
    if not stripped or stripped[0] not in "[{":
        return content
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return content


BlockRenderer = Callable[[dict[str, Any]], "str | None"]
"""返回 str = 用这个文本；返回 None = 走默认（原串）。"""


class TextConverter:
    """把 LangChain 内容降级为纯文本，并允许接入方按块类型扩展。"""

    def __init__(self) -> None:
        self._renderers: dict[str, BlockRenderer] = {}

    def register_block_renderer(self, block_type: str, renderer: BlockRenderer) -> None:
        """注册某个结构化块的文本渲染方式。"""
        self._renderers[block_type] = renderer

    def to_text(self, content: Any) -> str:
        """把字符串、内容块列表或结构化 JSON 转成可直接展示的文本。"""
        if isinstance(content, str):
            parsed = _parse_json_content(content)
            if isinstance(parsed, dict):
                renderer = self._renderers.get(str(parsed.get("type", "")))
                if renderer is not None:
                    rendered = renderer(parsed)
                    if rendered is not None:
                        return rendered
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if text is not None:
                        parts.append(str(text))
            return "".join(parts)
        return "" if content is None else str(content)


# ============================================================
# 中断序列化
# ============================================================

InterruptMatcher = Callable[[dict[str, Any]], bool]
InterruptExtractor = Callable[[dict[str, Any]], dict[str, Any]]


class InterruptSerializer:
    """把 LangGraph 中断对象 / 字典统一成前端协议。

    接入方可以先 ``register`` 自己的中断类型；未匹配的中断降级为
    ``unknown`` 并附带纯文本，保证前端始终有可展示的内容。
    """

    def __init__(self, text_converter: TextConverter) -> None:
        self._handlers: list[tuple[str, InterruptMatcher, InterruptExtractor]] = []
        self._text_converter = text_converter

    def register(
        self,
        interrupt_type: str,
        matcher: InterruptMatcher,
        extractor: InterruptExtractor,
    ) -> None:
        """按注册顺序匹配中断类型。"""
        self._handlers.append((interrupt_type, matcher, extractor))

    def serialize(self, interrupt: Any, thread_id: str) -> dict[str, Any]:
        """把一个中断对象转换为统一的 SSE / 历史响应结构。"""
        value = getattr(interrupt, "value", interrupt)
        interrupt_id = getattr(interrupt, "id", None)
        if isinstance(value, dict) and "value" in value and "id" in value:
            interrupt_id, value = value["id"], value["value"]
        value = value if isinstance(value, dict) else {}

        event: dict[str, Any] = {"type": "interrupt", "thread_id": thread_id}
        if interrupt_id:
            event["interrupt_id"] = str(interrupt_id)

        for interrupt_type, matcher, extractor in self._handlers:
            if matcher(value):
                event.update(interrupt_type=interrupt_type, **extractor(value))
                return event

        event.update(
            interrupt_type="unknown",
            interrupt_value=self._text_converter.to_text(value),
        )
        return event


# ============================================================
# 装配默认实例
# ============================================================

_text_converter = TextConverter()
_interrupt_serializer = InterruptSerializer(_text_converter)
# HITL 审批：DeepAgents / LangChain 的 interrupt_on 会返回 action_requests。
_interrupt_serializer.register(
    "hitl_approval",
    matcher=lambda value: "action_requests" in value,
    extractor=lambda value: {
        "action_requests": value["action_requests"],
        **({"review_configs": value["review_configs"]} if "review_configs" in value else {}),
    },
)
# HITL 补充信息：hitl_tool.request_additional_info 的中断结构。
_interrupt_serializer.register(
    "information_request",
    matcher=lambda value: value.get("type") == "information_request",
    extractor=lambda value: {
        "information_needed": value.get("information_needed", ""),
        "context": value.get("context", ""),
    },
)


# ============================================================
# 对外 API
# ============================================================


def content_to_text(content: Any) -> str:
    """LangChain 内容 → 可直接展示的纯文本。"""
    return _text_converter.to_text(content)


def serialize_interrupt(interrupt: Any, thread_id: str) -> dict[str, Any]:
    """checkpoint / 流式中断 → 统一前端协议。"""
    return _interrupt_serializer.serialize(interrupt, thread_id)


def make_session_title(content: str, max_length: int = 30) -> str:
    """从首条用户消息生成长度稳定的会话标题。"""
    normalized = content.strip()
    if not normalized:
        return "新会话"
    suffix = "..." if len(normalized) > max_length else ""
    return f"{normalized[:max_length]}{suffix}"
