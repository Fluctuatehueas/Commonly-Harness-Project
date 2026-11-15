"""运行时异常分类与指纹。

把 Agent 运行期抛出的异常映射为四类：

- ``transient``：网络抖动、模型 5xx/429、连接中断等，可自动重试；
- ``deterministic``：状态通道冲突、类型错误、递归超限等，重试必然复现；
- ``fatal``：配置/环境级问题，必须人工处理；
- ``cancelled``：HITL 暂停（``GraphBubbleUp``）、取消、生成器关闭，原样冒泡。

无法判定时按 ``fatal`` 处理：宁可不重试，也不要在确定性错误上烧钱。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    """异常类别；只有 TRANSIENT 允许自动重试。"""

    TRANSIENT = "transient"
    DETERMINISTIC = "deterministic"
    FATAL = "fatal"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ErrorInfo:
    """一次异常的分类结果，供监督者与日志共用。"""

    category: ErrorCategory
    error_class: str
    fingerprint: str
    retryable: bool
    message: str


# 图暂停 / 取消语义：不是错误，必须原样冒泡，否则 HITL 会永远等不到审批。
_CANCELLED_NAMES = {
    "GraphBubbleUp",
    "GraphInterrupt",
    "ParentCommand",
    "CancelledError",
    "KeyboardInterrupt",
    "SystemExit",
    "GeneratorExit",
}

# 可能由瞬时故障引起；仍需通过状态码二次确认，避免把 4xx 当成可重试。
_TRANSIENT_NAMES = {
    "APIConnectionError",
    "APIConnectionTimeoutError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
    "TimeoutError",
    "TimeoutException",
    "OperationalError",
    "InterfaceError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "OverloadedError",
}

# 确定性错误：同一输入重放必然复现，重试只会拖长用户等待。
_DETERMINISTIC_NAMES = {
    "InvalidUpdateError",
    "GraphRecursionError",
    "ValidationError",
    "TypeError",
    "ValueError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "NotImplementedError",
}

_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_QUOTED = re.compile(r"'[^']{0,80}'")
_WS = re.compile(r"\s+")

_MESSAGE_MAX = 200


def _normalize(text: str) -> str:
    """抹掉每次都会变化的部分，保证同一处失败得到稳定指纹。"""
    normalized = _ADDR.sub("0x?", text)
    normalized = _UUID.sub("<uuid>", normalized)
    normalized = _QUOTED.sub("<str>", normalized)
    normalized = _WS.sub(" ", normalized).strip()
    return normalized[:_MESSAGE_MAX]


def fingerprint(exc: BaseException, node: str = "") -> str:
    """异常指纹：类型 + 归一化消息 + 节点，用于无进展检测与日志聚合。"""
    raw = f"{type(exc).__name__}|{_normalize(str(exc))}|{node}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _status_code(exc: BaseException) -> int | None:
    """读取 HTTP 状态码；没有或类型不对时返回 None。"""
    for attr in ("status_code", "http_status", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def classify(exc: BaseException, node: str = "") -> ErrorInfo:
    """把异常归类，并给出是否可重试的结论。

    按 MRO 匹配异常名：自定义异常大多是标准异常或 SDK 异常的子类，
    只看最具体的类名会漏判（例如 ``MyTimeout(TimeoutError)``）。
    """
    name = type(exc).__name__
    names = {cls.__name__ for cls in type(exc).__mro__}
    message = _normalize(str(exc)) or name
    fp = fingerprint(exc, node)

    def build(category: ErrorCategory, retryable: bool) -> ErrorInfo:
        return ErrorInfo(
            category=category,
            error_class=name,
            fingerprint=fp,
            retryable=retryable,
            message=message,
        )

    if names & _CANCELLED_NAMES:
        return build(ErrorCategory.CANCELLED, False)

    # 先看状态码：429 与 5xx 可重试，其余 4xx 属于请求本身的问题。
    status = _status_code(exc)
    if status is not None:
        if status == 429 or status >= 500:
            return build(ErrorCategory.TRANSIENT, True)
        return build(ErrorCategory.FATAL, False)

    if names & _TRANSIENT_NAMES:
        return build(ErrorCategory.TRANSIENT, True)
    if names & _DETERMINISTIC_NAMES:
        return build(ErrorCategory.DETERMINISTIC, False)

    # RuntimeError 在本项目里承载"配置/环境级错误"，文案可读，直接透传。
    return build(ErrorCategory.FATAL, False)


__all__ = ["ErrorCategory", "ErrorInfo", "classify", "fingerprint"]
