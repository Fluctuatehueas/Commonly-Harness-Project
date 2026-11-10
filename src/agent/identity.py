"""请求级用户身份上下文。

HTTP 层从 ``X-User-Id`` 头解析身份后写入 ContextVar；Agent 层、MCP/RAG
运行时、技能目录和模型配置都通过本模块读取当前用户，避免把 user_id
层层透传到每个工具与中间件。
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token

_USER_ID_VAR: ContextVar[str] = ContextVar("harness_user_id", default="")
_AGENT_ID_VAR: ContextVar[str] = ContextVar("harness_agent_id", default="default")
_SAFE_USER = re.compile(r"[^A-Za-z0-9_.-]")


def safe_user_id(value: str | None, fallback: str = "anonymous") -> str:
    """把任意用户标识转换为可安全用作目录名的形式。"""
    cleaned = _SAFE_USER.sub("_", str(value or "").strip())[:64]
    return cleaned or fallback


def safe_agent_id(value: str | None, fallback: str = "default") -> str:
    """Agent 标识与用户标识使用同一套安全规则。"""
    return safe_user_id(value, fallback=fallback)


def set_current_user_id(value: str | None) -> Token[str]:
    # 没有请求头时写入空串，让 ``current_user_id(default)`` 能正常回退；
    # 不能写成 "anonymous"，否则会覆盖请求体/查询参数里的显式 user_id。
    cleaned = safe_user_id(value, fallback="") if value else ""
    return _USER_ID_VAR.set(cleaned)


def reset_current_user_id(token: Token[str]) -> None:
    try:
        _USER_ID_VAR.reset(token)
    except ValueError:
        # Token 跨上下文重置时忽略，避免中间件 finally 抛错。
        pass


def current_user_id(default: str = "anonymous") -> str:
    return _USER_ID_VAR.get() or default


def set_current_agent_id(value: str | None) -> Token[str]:
    return _AGENT_ID_VAR.set(safe_agent_id(value))


def reset_current_agent_id(token: Token[str]) -> None:
    try:
        _AGENT_ID_VAR.reset(token)
    except ValueError:
        pass


def current_agent_id(default: str = "default") -> str:
    return _AGENT_ID_VAR.get() or default
