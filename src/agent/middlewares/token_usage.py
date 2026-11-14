"""会话 Token 消耗统计中间件。

统计口径区分「上下文占用」与「真实计费」，两者不能混用：

1. ``input``：本轮**最后一次**模型调用的输入 Token，代表当前上下文占用；
2. ``input_spent``：本轮所有模型调用的输入之和，代表真实计费；
3. ``output``：本轮所有模型调用的输出之和（含工具决策与思考）；
4. ``total``：``input_spent + output``，即本轮真实计费总量。

不能把每次调用的 input 都写进 ``input``：Agent 工具循环会反复重传完整上下文，
累加后一轮就可能是几百万甚至上千万 Token，前端上下文圆环会被直接打满。
模型没返回 usage 时按文本长度估算输出（中文≈1 字 1 token，其他≈4 字符 1 token），
避免整轮显示 0。

一轮的合计以该轮最终回答的消息 ID 为键写入 Store，与前端气泡展示一一对应。
写入失败只告警，不影响 Agent 主流程。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from agent.middlewares.trajectory import _message_text, _usage_tokens
from agent.usage_store import append_usage_round


logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """粗估 token：中日韩字符按 1 token/字，其余按 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return int(cjk + (len(text) - cjk) / 4)


def _reasoning_text(message: Any) -> str:
    """读取推理模型的思考内容（不同厂商字段不同）。"""
    additional = getattr(message, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = additional.get(key)
            if value:
                return str(value)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
        )
    return ""


def _tool_call_text(message: Any) -> str:
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return ""
    try:
        return json.dumps(calls, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(calls)


def _turn_usage(messages: list[Any]) -> dict[str, int]:
    """计算本轮用量：input 取最后一次调用，input_spent 累加全部调用。

    工具循环里每次请求都会重传完整上下文；如果 input 也累加，一轮的
    「上下文占用」会被放大到与真实窗口无关的量级。
    """
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", None) == "human":
            start = index + 1
            break

    input_latest = 0
    input_spent = 0
    output_total = 0
    for message in messages[start:]:
        if getattr(message, "type", None) != "ai":
            continue
        usage = _usage_tokens(message)
        if usage:
            current_input = int(usage.get("input", 0) or 0)
            if current_input:
                # 消息按时间顺序排列，最后一条有 usage 的 AI 即最新上下文占用。
                input_latest = current_input
                input_spent += current_input
            output_total += int(usage.get("output", 0) or 0)
            continue
        # 没有计费数据时至少把可见输出算进去：正文 + 思考 + 工具参数。
        visible = f"{_message_text(message)}{_reasoning_text(message)}{_tool_call_text(message)}"
        output_total += _estimate_tokens(visible)

    # 有输入却没有输出数据时按可见文本兜底，避免整轮显示 0 输出。
    if input_latest and output_total == 0:
        visible_all = "".join(
            f"{_message_text(message)}{_reasoning_text(message)}{_tool_call_text(message)}"
            for message in messages[start:]
            if getattr(message, "type", None) == "ai"
        )
        output_total = _estimate_tokens(visible_all)

    return {
        "input": input_latest,
        "input_spent": input_spent,
        "output": output_total,
        "total": input_spent + output_total,
    }


class TokenUsageMiddleware(AgentMiddleware):
    """每轮结束后把整轮 Token 消耗累计写入 Store。"""

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
        # 「不带 tool_calls 且带文本的 AI 消息」才代表本轮最终回答，
        # 这样同一轮不会被重复累计。
        last = messages[-1]
        if getattr(last, "type", None) != "ai":
            return None
        if getattr(last, "tool_calls", None):
            return None
        if not _message_text(last):
            return None
        usage = _turn_usage(messages)
        if not usage.get("total"):
            return None
        message_id = str(getattr(last, "id", "") or f"{thread_id}:{len(messages)}")
        try:
            await append_usage_round(
                store,
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                input_tokens=usage.get("input", 0),
                input_spent_tokens=usage.get("input_spent", 0),
                output_tokens=usage.get("output", 0),
                total_tokens=usage.get("total", 0),
            )
        except Exception:
            logger.warning("写入会话 Token 统计失败", exc_info=True)
        return None
