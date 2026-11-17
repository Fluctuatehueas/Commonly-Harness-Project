"""Agent 验证能力入口（纯观测）。

对外保留原有 ``rule_check`` / ``review_against_example``，新增统一 runner。
验证结论只写轨迹与信号，不参与回答放行。
"""

from __future__ import annotations

from agent.verification.legacy import (
    MIN_OUTPUT_CHARS,
    review_against_example,
    rule_check,
)
from agent.verification.runner import (
    VerificationRunner,
    get_verification_runner,
    point_enabled,
)
from agent.verification.types import (
    VerificationIssue,
    VerificationLevel,
    VerificationPoint,
    VerificationRecord,
    VerificationRequest,
)

__all__ = [
    "MIN_OUTPUT_CHARS",
    "VerificationIssue",
    "VerificationLevel",
    "VerificationPoint",
    "VerificationRecord",
    "VerificationRequest",
    "VerificationRunner",
    "get_verification_runner",
    "point_enabled",
    "review_against_example",
    "rule_check",
]
