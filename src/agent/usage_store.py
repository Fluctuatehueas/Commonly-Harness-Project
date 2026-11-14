"""会话 Token 消耗的 Store 读写。

统计由 ``TokenUsageMiddleware`` 在每轮 after_agent 写入，命名空间：

    ("usage", <user_id>, <thread_id>) -> key: "session"

value 结构::

    {
      "rounds": [
        {
          "message_id": "...",
          "input": 6056,
          "input_spent": 38210,
          "output": 42,
          "total": 38252,
          "at": "iso",
        }
      ],
      "session": {"input": 6056, "output": 42, "total": 6098, "spent": 38252}
    }

``input`` 是本轮最后一次模型调用的上下文占用；``input_spent`` 是本轮全部
调用的输入之和（真实计费）。``rounds`` 是唯一事实源，按写入顺序追加；
``session`` 按「最新上下文 + 历轮输出 + 历轮真实计费」计算。
以最终回答消息 ID 去重，保证同一轮重放（如中断恢复）不会重复计数；
即使历史消息随后被摘要压缩掉，累计值依然保留。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.config import MODEL_CONTEXT_LIMIT, context_limit_for

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
    ``spent`` 是真实计费口径（历轮 input + output 之和），用于展示
    “本会话总消耗”；撤回消息不会回退该值。
    """
    latest_input = int(rounds[-1].get("input") or 0) if rounds else 0
    output_sum = sum(int(entry.get("output") or 0) for entry in rounds)
    # 旧记录没有 input_spent：回退到 input，保证历史会话的费用仍可累计。
    input_sum = sum(
        int(entry.get("input_spent", entry.get("input", 0)) or 0)
        for entry in rounds
    )
    return {
        "input": latest_input,
        "output": output_sum,
        "total": latest_input + output_sum,
        "spent": input_sum + output_sum,
        "rounds": len(rounds),
    }


async def read_usage(store: Any, *, user_id: str, thread_id: str) -> dict[str, Any]:
    """读取会话用量；无记录时返回空结构。

    ``session.context_window`` 与 ``session.context_used`` 是运行时附加字段，
    让前端不用再维护本地上下文窗口常量。
    """
    if store is None or not user_id or not thread_id:
        return _with_context_window({"rounds": [], "session": _session_summary([])}, user_id)
    item = await store.aget(usage_namespace(user_id, thread_id), SESSION_KEY)
    if item is None:
        return _with_context_window({"rounds": [], "session": _session_summary([])}, user_id)
    value = dict(item.value or {})
    rounds = [dict(entry) for entry in value.get("rounds", []) if isinstance(entry, dict)]
    return _with_context_window(
        {"rounds": rounds, "session": _session_summary(rounds)}, user_id
    )


def _with_context_window(
    usage: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    """给会话用量补上真实上下文窗口与当前占用。

    窗口取该用户生效的模型配置，而不是进程启动时读到的全局常量：
    用户在设置页或 Agent 档案里调整上下文上限后，前端圆环应立即跟随。
    """
    session = dict(usage.get("session") or {})
    window = 0
    if user_id:
        try:
            window = int(context_limit_for(user_id))
        except Exception:
            window = 0
    session.setdefault("context_window", window or int(MODEL_CONTEXT_LIMIT))
    session.setdefault("context_used", int(session.get("input") or 0))
    result = dict(usage)
    result["session"] = session
    return result


async def append_usage_round(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    message_id: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    input_spent_tokens: int = 0,
) -> None:
    """追加一轮的最终回答用量；同一消息 ID 重复写入时保持幂等。

    ``input_tokens`` 是上下文占用，``input_spent_tokens`` 是本轮真实计费输入；
    旧调用方不传后者时按上下文占用计，保持向后兼容。
    """
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
            "input_spent": int(input_spent_tokens or input_tokens),
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
