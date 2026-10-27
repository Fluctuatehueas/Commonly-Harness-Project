"""会话 Token 消耗的 Store 读写。

统计由 ``TokenUsageMiddleware`` 在每轮 after_agent 写入，命名空间：

    ("usage", <user_id>, <thread_id>) -> key: "session"

value 结构::

    {
      "rounds": [
        {"message_id": "...", "input": 6056, "output": 42, "total": 6098, "at": "iso"}
      ],
      "session": {"input": 6056, "output": 42, "total": 6098}
    }

``rounds`` 是唯一事实源，按写入顺序追加；``session`` 是它的前缀和。
以最终回答消息 ID 去重，保证同一轮重放（如中断恢复）不会重复计数；
即使历史消息随后被摘要压缩掉，累计值依然保留。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

USAGE_NAMESPACE_PREFIX = ("usage",)
SESSION_KEY = "session"


def usage_namespace(user_id: str, thread_id: str) -> tuple[str, str, str]:
    """返回某个会话的用量命名空间。"""
    return (*USAGE_NAMESPACE_PREFIX, user_id, thread_id)


def _session_summary(rounds: list[dict[str, Any]]) -> dict[str, int]:
    """计算会话口径的汇总。

    ``input`` 取最近一轮的输入 Token（代表当前上下文占用），
    ``output`` 是历轮新增生成 Token 之和。
    不能把每轮 input 直接相加：system prompt / 工具 / 技能 / 记忆
    每轮都会被重复送入模型，逐轮求和会让会话数字成倍虚高。
    """
    latest_input = int(rounds[-1].get("input") or 0) if rounds else 0
    output_sum = sum(int(entry.get("output") or 0) for entry in rounds)
    return {
        "input": latest_input,
        "output": output_sum,
        "total": latest_input + output_sum,
        "rounds": len(rounds),
    }


async def read_usage(store: Any, *, user_id: str, thread_id: str) -> dict[str, Any]:
    """读取会话用量；无记录时返回空结构。"""
    if store is None or not user_id or not thread_id:
        return {"rounds": [], "session": _session_summary([])}
    item = await store.aget(usage_namespace(user_id, thread_id), SESSION_KEY)
    if item is None:
        return {"rounds": [], "session": _session_summary([])}
    value = dict(item.value or {})
    rounds = [dict(entry) for entry in value.get("rounds", []) if isinstance(entry, dict)]
    return {"rounds": rounds, "session": _session_summary(rounds)}


async def append_usage_round(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    message_id: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> None:
    """追加一轮的最终回答用量；同一消息 ID 重复写入时保持幂等。"""
    if store is None or not user_id or not thread_id or not message_id:
        return
    namespace = usage_namespace(user_id, thread_id)
    item = await store.aget(namespace, SESSION_KEY)
    value = dict(item.value) if item is not None else {}
    rounds = [dict(entry) for entry in value.get("rounds", []) if isinstance(entry, dict)]
    if any(str(entry.get("message_id", "")) == message_id for entry in rounds):
        return
    rounds.append(
        {
            "message_id": message_id,
            "input": int(input_tokens),
            "output": int(output_tokens),
            "total": int(total_tokens),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await store.aput(
        namespace,
        SESSION_KEY,
        {"rounds": rounds, "session": _session_summary(rounds)},
        index=False,
    )


async def delete_usage(store: Any, *, user_id: str, thread_id: str) -> None:
    """删除会话用量，与会话同生命周期。"""
    if store is None or not user_id or not thread_id:
        return
    await store.adelete(usage_namespace(user_id, thread_id), SESSION_KEY)
