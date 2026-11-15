"""Agent 运行时监督：异常分类与有界重试。

对外只有两个入口：``RunSupervisor``（包装流式调用）与 ``RuntimeNotice``
（面向用户的提示）。异常分类与指纹在 ``errors`` 中，保持纯函数、可单测。
"""

from __future__ import annotations

from .errors import ErrorCategory, ErrorInfo, classify, fingerprint
from .supervisor import RunSupervisor, RuntimeNotice

__all__ = [
    "ErrorCategory",
    "ErrorInfo",
    "RunSupervisor",
    "RuntimeNotice",
    "classify",
    "fingerprint",
]
