"""上下文预算计算。

窗口口径只认 ``HARNESS_MODEL_CONTEXT_LIMIT``，不再使用模型 profile 的
1,000,000 窗口。这样压缩触发点与前端展示、监控告警保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import agent.config as config


@dataclass(frozen=True)
class ContextBudget:
    """一次会话的上下文预算分解。"""

    window: int
    reserve: int
    usable: int
    soft: int
    hard: int
    keep: int

    @property
    def soft_pct(self) -> float:
        return round(self.soft / self.window, 4) if self.window else 0.0

    @property
    def hard_pct(self) -> float:
        return round(self.hard / self.window, 4) if self.window else 0.0

    def as_dict(self) -> dict[str, int | float]:
        """给前端事件与轨迹回执使用的扁平结构。"""
        return {
            "window": self.window,
            "reserve": self.reserve,
            "usable": self.usable,
            "soft": self.soft,
            "hard": self.hard,
            "keep": self.keep,
            "soft_pct": self.soft_pct,
            "hard_pct": self.hard_pct,
        }


def _ratio(value: float, fallback: float) -> float:
    """比例配置必须落在 (0, 1]，异常值回退默认。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if 0 < number <= 1 else fallback


def build_budget(window: int | None = None) -> ContextBudget:
    """按窗口推导软/硬水位与保留预算。

    输出预留 ``R = max(8000, 0.12 * window)``，剩余为可用预算 ``B``；
    soft/hard/keep 均是相对 ``B`` 的比例。比例非法时回退文档默认值。
    """
    limit = max(int(window or config.MODEL_CONTEXT_LIMIT), 1)
    reserve = max(8000, int(limit * 0.12))
    usable = max(limit - reserve, 1)

    trigger = _ratio(config.CONTEXT_COMPACT_TRIGGER, 0.70)
    hard = _ratio(config.CONTEXT_COMPACT_HARD, 0.88)
    keep = _ratio(config.CONTEXT_COMPACT_KEEP, 0.25)
    # 保证 keep < trigger < hard，否则压缩后仍可能超预算。
    if not keep < trigger:
        keep = 0.25
    if not trigger < hard:
        hard = 0.88
        trigger = 0.70

    return ContextBudget(
        window=limit,
        reserve=reserve,
        usable=usable,
        soft=max(int(usable * trigger), 1),
        hard=max(int(usable * hard), 1),
        keep=max(int(usable * keep), 1),
    )
