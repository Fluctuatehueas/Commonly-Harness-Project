"""会话 Token 消耗统计中间件。

模型调用级别的数据源：直接读取 LangChain 消息上的 ``usage_metadata``
（DeepSeek 等 OpenAI 兼容模型返回的真实计费数），在每轮 ``after_agent``：

1. 取本轮最终回答（最后一条带文本的 AI 消息）的 usage 作为单轮消耗；
2. 以该消息 ID 为键把单轮消耗追加进 Store 的会话累计。

统计口径是"仅最后一次回答的调用"，与前端气泡下方展示一致；
会话累计 = 历轮单轮消耗之和，消息被摘要压缩后依然保留。
写入失败只告警，不影响 Agent 主流程。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from agent.middlewares.trajectory import _message_text, _usage_tokens
from agent.usage_store import append_usage_round


logger = logging.getLogger(__name__)


class TokenUsageMiddleware(AgentMiddleware):
    """每轮结束后把最终回答的 Token 消耗累计写入 Store。"""

    async def aafter_agent(self, state: dict[str, Any], runtime: Runtime) -> None:
        context = getattr(runtime, "context", None)
        user_id = str(getattr(context, "user_id", "") or "")
        thread_id = str(getattr(context, "thread_id", "") or "")
        store = getattr(runtime, "store", None)
        if not user_id or not thread_id or store is None:
            return None
        messages = list((state or {}).get("messages", []) or [])
        if not messages:
            return None
        # after_agent 可能在工具循环的中间节点也触发；只有最后一条消息是
        # 「不带 tool_calls 且带文本的 AI 消息」才代表本轮最终回答。
        # 这样中间的前导话术/工具决策轮不会被重复计入会话统计。
        last = messages[-1]
        if getattr(last, "type", None) != "ai":
            return None
        if getattr(last, "tool_calls", None):
            return None
        if not _message_text(last):
            return None
        usage = _usage_tokens(last)
        if not usage:
            return None
        message_id = str(getattr(last, "id", "") or f"{thread_id}:{len(messages)}")
        try:
            await append_usage_round(
                store,
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                input_tokens=usage.get("input", 0),
                output_tokens=usage.get("output", 0),
                total_tokens=usage.get("total", 0),
            )
        except Exception:
            logger.warning("写入会话 Token 统计失败", exc_info=True)
        return None
