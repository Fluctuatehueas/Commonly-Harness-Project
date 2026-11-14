"""检查点打分：把 D 的四个可观测分量聚合成是否重规划的概率。

打分只做决策，不执行重规划；执行见 ``replanner.py``。
"""

from __future__ import annotations

import math
from typing import Optional

from .config import TierConfig
from .models import CheckpointDecision, PlanItem, Tier

_LEVEL = {"low": 0.0, "mid": 0.5, "high": 1.0}


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def boundary_ratio(done: int, total: int) -> float:
    """预算使用比例的代理：已完成条目 / 总条目，避免依赖真实 token 计量。"""
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, done / total))


def score_checkpoint(
    *,
    tier: Tier,
    impact: str = "mid",
    uncertainty: str = "mid",
    user_delta: bool = False,
    global_scope: bool = False,
    budget_ratio: float = 0.0,
    irreversible: bool = False,
    cfg: Optional[TierConfig] = None,
) -> CheckpointDecision:
    """按 β·z + bias[tier] 打分，并套用硬门控与阈值。"""
    cfg = cfg or TierConfig()

    # 硬门控优先于概率：不可逆操作必须人工确认，全局新需求必须完整重规划。
    if irreversible:
        return CheckpointDecision(
            score=1.0,
            action="hitl",
            scope="subtree",
            reason="条目包含不可逆操作，必须先由用户确认",
        )
    if global_scope:
        return CheckpointDecision(
            score=1.0,
            action="replan",
            scope="full",
            reason="新需求影响全局目标或验收标准",
        )

    z = [
        _LEVEL.get(impact, 0.5),
        _LEVEL.get(uncertainty, 0.5),
        0.5 if user_delta else 0.0,
        max(0.0, min(1.0, budget_ratio)),
    ]
    beta = cfg.beta
    raw = sum(weight * value for weight, value in zip(beta, z))
    raw += float(cfg.bias.get(tier.value, 0.0))
    score = _sigmoid(raw)

    if score >= cfg.p_replan:
        return CheckpointDecision(
            score=score,
            action="replan",
            scope="subtree",
            reason=f"检查点得分 {score:.3f} >= {cfg.p_replan}，局部重规划",
        )
    if score >= cfg.p_reorder:
        return CheckpointDecision(
            score=score,
            action="reorder",
            scope="reorder",
            reason=f"检查点得分 {score:.3f} 位于排序区间，只调整执行顺序",
        )
    return CheckpointDecision(
        score=score,
        action="continue",
        reason=f"检查点得分 {score:.3f} < {cfg.p_reorder}，继续执行",
    )


def needs_checkpoint(
    item: PlanItem,
    *,
    index: int,
    journal: int,
    cfg: TierConfig,
) -> bool:
    """检查点时机：显式标记、重试过、每 N 条兜底。

    写入路径不触发检查点：它只决定同波次是否冲突，不代表执行风险。
    """
    if item.checkpoint:
        return True
    if item.attempts > 1:
        return True
    return cfg.checkpoint_every > 0 and index > 0 and journal % cfg.checkpoint_every == 0


__all__ = ["boundary_ratio", "needs_checkpoint", "score_checkpoint"]
