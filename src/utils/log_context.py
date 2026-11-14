"""日志关联上下文。

一次对话涉及的标识（trace_id / run_id / thread_id / user_id / agent_id）通过
``contextvars`` 携带，业务代码不再逐层传参；日志过滤器在写入时统一补齐。

``contextvars`` 在 asyncio 协程之间自动继承，但**不会跨线程**。这也是运行时
异常处理坚持"不把 Agent 挪到线程池"的原因之一：挪线程等于所有关联上下文都要
手工搬运，漏一个就断链。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

# 一次性存放全部关联字段；只做整体替换，避免可变默认值被共享修改。
_LOG_FIELDS: ContextVar[dict[str, Any]] = ContextVar("harness_log_fields", default={})


def new_trace_id() -> str:
    """生成一次请求的追踪 ID。"""
    return f"tr_{uuid.uuid4().hex[:12]}"


def new_run_id() -> str:
    """生成一次图运行的 ID；同一次运行的重试共用它。"""
    return f"run_{uuid.uuid4().hex[:12]}"


def bind_log_fields(**fields: Any) -> None:
    """绑定关联字段；空值不覆盖已有值，便于"先绑会话再绑 run"。"""
    clean = {key: value for key, value in fields.items() if value not in (None, "")}
    if not clean:
        return
    _LOG_FIELDS.set({**_LOG_FIELDS.get(), **clean})


def clear_log_fields() -> None:
    """请求结束时清理，避免同一次事件循环内的后续任务继承到错误上下文。"""
    _LOG_FIELDS.set({})


def current_log_fields() -> dict[str, Any]:
    """返回当前上下文快照（副本）。"""
    return dict(_LOG_FIELDS.get())


__all__ = [
    "bind_log_fields",
    "clear_log_fields",
    "current_log_fields",
    "new_run_id",
    "new_trace_id",
]
