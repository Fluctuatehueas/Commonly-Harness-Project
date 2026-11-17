"""每 run 的验证预算。

计数器放在 HarnessContext 上（进程内、随请求创建），不落库。
所有读写都对缺失字段容错，避免验证影响主流程。
"""

from __future__ import annotations

import random
from typing import Any

from agent.config import (
    VERIFICATION_MAX_DELEGATIONS,
    VERIFICATION_MAX_JUDGE_CALLS,
)


def reset(context: Any) -> None:
    if context is None:
        return
    for key in (
        "verification_judge_calls",
        "verification_delegations",
        "verification_blocking",
    ):
        try:
            setattr(context, key, 0)
        except Exception:  # noqa: BLE001 - 上下文不可写时跳过
            pass


def _count(context: Any, key: str) -> int:
    try:
        return int(getattr(context, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def can_call_judge(context: Any) -> bool:
    return _count(context, "verification_judge_calls") < max(
        VERIFICATION_MAX_JUDGE_CALLS, 0
    )


def note_judge_call(context: Any) -> None:
    if context is None:
        return
    try:
        setattr(
            context,
            "verification_judge_calls",
            _count(context, "verification_judge_calls") + 1,
        )
    except Exception:  # noqa: BLE001
        pass


def can_delegate(context: Any) -> bool:
    return _count(context, "verification_delegations") < max(
        VERIFICATION_MAX_DELEGATIONS, 0
    )


def note_delegation(context: Any) -> None:
    if context is None:
        return
    try:
        setattr(
            context,
            "verification_delegations",
            _count(context, "verification_delegations") + 1,
        )
    except Exception:  # noqa: BLE001
        pass


def sample_hit(rate: float) -> bool:
    """按比例抽样；rate >= 1 时恒命中。"""
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return True
    if value >= 1:
        return True
    if value <= 0:
        return False
    return random.random() < value


__all__ = [
    "can_call_judge",
    "can_delegate",
    "note_delegation",
    "note_judge_call",
    "reset",
    "sample_hit",
]
