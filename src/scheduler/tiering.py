"""分档核心：几何聚合 + 有序 logistic + 滞回。纯函数，便于单测。"""

import math
from functools import lru_cache
from typing import Iterable, Optional

from .config import TierConfig
from .models import TIER_ORDER, FeatureVector, Tier, TierDecision


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def complexity_score(x: FeatureVector, weights: dict[str, float], eps: float = 1e-3) -> float:
    """加权几何平均，D=C·I·R·N 乘积型的平滑归一化版本。

    只在已观测维度上归一：把缺失维度按 0.5 代入并保留其权重，等价于把 S
    往 0.5 拉，P0 有三到四维结构性缺失时会把所有任务压成同一档。
    """
    values = x.as_dict()
    skip = set(x.missing)
    keys = [k for k in weights if k not in skip] or list(weights)
    total_w = sum(weights[k] for k in keys)
    if total_w <= 0:
        raise ValueError("权重和必须为正")
    num = sum(weights[k] * math.log(max(values[k], eps) + eps) for k in keys)
    return max(0.0, min(1.0, math.exp(num / total_w)))


def memberships(s: float, theta1: float, theta2: float, alpha: float) -> dict[str, float]:
    """有序 logistic 的三档隶属度，三者非负且和为 1。"""
    s1 = sigmoid(alpha * (s - theta1))
    s2 = sigmoid(alpha * (s - theta2))
    return {"EZ": 1.0 - s1, "MD": max(0.0, s1 - s2), "HD": max(0.0, s2)}


def _hysteresis_boundary(from_tier: Tier, to_tier: Tier, cfg: TierConfig) -> float:
    """相邻档位的实际切换边界。

    注意：有序 logistic 的 argmax 分界并不等于 theta。
    例如 theta1 只是 EZ/MD 隶属度的中点，而 EZ 与 MD 概率相等的位置会更靠右。
    滞回必须围绕真实切换点，否则永远不会触发。
    """
    b1, b2 = decision_boundaries(cfg.theta1, cfg.theta2, cfg.alpha)
    low = min(TIER_ORDER.index(from_tier), TIER_ORDER.index(to_tier))
    return b1 if low == 0 else b2


@lru_cache(maxsize=128)
def decision_boundaries(theta1: float, theta2: float, alpha: float) -> tuple[float, float]:
    """返回 argmax 意义下的两条真实边界：EZ→MD 与 MD→HD。"""

    def tier_at(s: float) -> str:
        probs = memberships(s, theta1, theta2, alpha)
        return max(probs, key=probs.get)

    def first_s(predicate) -> float:
        if not predicate(1.0):
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if predicate(mid):
                hi = mid
            else:
                lo = mid
        return hi

    b1 = first_s(lambda s: tier_at(s) != "EZ")
    b2 = first_s(lambda s: tier_at(s) == "HD")
    return b1, b2


def decide_tier(
    x: FeatureVector,
    cfg: TierConfig,
    prev_tier: Optional[Tier] = None,
    hard_override: Optional[Tier] = None,
) -> TierDecision:
    """完整决策：硬规则 → 平滑分档 → 滞回 → 低置信度升档 → 缺失兜底。"""
    s = complexity_score(x, cfg.weights)
    probs = memberships(s, cfg.theta1, cfg.theta2, cfg.alpha)

    if hard_override is not None:
        return TierDecision(
            tier=hard_override,
            s=s,
            probs=probs,
            rule_override=str(hard_override.value),
            reason="硬规则覆盖，跳过概率决策",
        )

    raw = max(probs, key=probs.get)
    tier = Tier(raw)
    reason = f"argmax={raw}, s={s:.3f}"

    # 1) 滞回：相邻档位切换必须越过 theta±delta
    if prev_tier is not None and prev_tier != tier:
        prev_idx = TIER_ORDER.index(prev_tier)
        new_idx = TIER_ORDER.index(tier)
        if abs(new_idx - prev_idx) == 1:
            boundary = _hysteresis_boundary(prev_tier, tier, cfg)
            allowed = s > boundary + cfg.delta if new_idx > prev_idx else s < boundary - cfg.delta
            if not allowed:
                tier = prev_tier
                reason = f"滞回拦截（边界 {boundary:.3f} ± {cfg.delta}），维持 {prev_tier.value}"

    # 2) 低置信度升一档：低估复杂度的代价高于高估
    upgraded = False
    confidence = probs[tier.value]
    if confidence < cfg.tau and tier != Tier.HD:
        tier = TIER_ORDER[TIER_ORDER.index(tier) + 1]
        upgraded = True
        reason = f"低置信度 {confidence:.3f} < tau={cfg.tau}，升档到 {tier.value}"

    # 3) 缺失维度兜底：非结构性缺 3 维及以上不允许 EZ
    unexpected = [d for d in x.missing if d not in set(x.structural_missing)]
    if len(unexpected) >= 3 and tier == Tier.EZ:
        tier = Tier.MD
        reason = f"非结构性缺失 {len(unexpected)} 维，最低 MD"

    return TierDecision(tier=tier, s=s, probs=probs, upgraded=upgraded, reason=reason)


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.5
    pos = (len(sorted_values) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def suggest_theta(scores: Iterable[float], q1: float = 0.327, q2: float = 0.840) -> tuple[float, float]:
    """按 Brick 数据先验的分位点给出边界，供收集到影子数据后校准。"""
    values = sorted(scores)
    return _quantile(values, q1), _quantile(values, q2)
