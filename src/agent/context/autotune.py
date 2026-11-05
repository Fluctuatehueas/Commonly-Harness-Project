"""压缩评估驱动的阈值自动调参。

只做三件事：
1. 累计压缩评估样本（保真、时机、节省）；
2. 达到采样窗口后给出阈值补丁；
3. 通过注册的 applier 把补丁写入配置并重建 Agent 图。

调参有硬边界：keep < trigger < hard，且每次只允许 ±0.05 的小步调整，
避免一次评估波动把阈值推到极端。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class TunerStats:
    samples: int = 0
    early: int = 0
    forced: int = 0
    low_fidelity: int = 0
    saved_tokens: int = 0
    fidelity_sum: float = 0.0
    last_adjust_sample: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "early": self.early,
            "forced": self.forced,
            "low_fidelity": self.low_fidelity,
            "saved_tokens": self.saved_tokens,
            "avg_fidelity": round(self.fidelity_sum / self.samples, 4)
            if self.samples
            else 0.0,
        }


_STATS = TunerStats()
# API 层注册：把补丁写入 .env 并重建 Agent 图。
_APPLIER: Callable[[dict[str, float], str], Awaitable[None]] | None = None


def set_applier(
    applier: Callable[[dict[str, float], str], Awaitable[None]] | None,
) -> None:
    global _APPLIER
    _APPLIER = applier


def snapshot() -> dict[str, Any]:
    return _STATS.snapshot()


def record(evaluation: dict[str, Any]) -> bool:
    """记录一次评估；返回是否到达调参窗口。"""
    if not evaluation or evaluation.get("status") in {"skipped", "failed"}:
        return False
    _STATS.samples += 1
    timing = str(evaluation.get("timing") or "")
    fidelity = float(evaluation.get("fidelity") or 0)
    _STATS.fidelity_sum += fidelity
    if timing == "early":
        _STATS.early += 1
    if timing == "forced":
        _STATS.forced += 1
    if fidelity < 0.9:
        _STATS.low_fidelity += 1
    _STATS.saved_tokens += int(evaluation.get("saved_tokens") or 0)

    from agent.config import CONTEXT_AUTOTUNE_INTERVAL

    interval = max(int(CONTEXT_AUTOTUNE_INTERVAL), 5)
    return (
        _STATS.samples - _STATS.last_adjust_sample >= interval
        and _STATS.samples > 0
    )


def recommend(
    current: dict[str, Any],
    stats: dict[str, Any],
) -> tuple[dict[str, float], str]:
    """根据样本给出小步补丁；无建议时返回空补丁。"""
    trigger = float(current.get("trigger", 0.70))
    hard = float(current.get("hard", 0.88))
    keep = float(current.get("keep", 0.25))
    samples = max(int(stats.get("samples") or 0), 1)
    early_ratio = float(stats.get("early") or 0) / samples
    forced_ratio = float(stats.get("forced") or 0) / samples
    avg_fidelity = float(stats.get("avg_fidelity") or 1.0)

    patch: dict[str, float] = {}
    reasons: list[str] = []

    if early_ratio >= 0.3:
        keep = min(keep + 0.05, 0.35)
        trigger = max(trigger - 0.05, 0.50)
        patch.update({"keep": round(keep, 3), "trigger": round(trigger, 3)})
        reasons.append(f"过早压缩占比 {early_ratio:.0%}，提高保留预算并降低软水位")

    if forced_ratio >= 0.5:
        hard = max(hard - 0.05, trigger + 0.05)
        trigger = max(trigger - 0.03, 0.50)
        patch.update({"hard": round(hard, 3), "trigger": round(trigger, 3)})
        reasons.append(f"强制触发占比 {forced_ratio:.0%}，提前软/硬水位")

    if avg_fidelity < 0.9:
        keep = min(keep + 0.05, 0.35)
        patch["keep"] = round(keep, 3)
        reasons.append(f"平均锚点保真 {avg_fidelity:.0%}，提高保留预算")

    if not patch:
        return {}, "样本窗口内未发现需要调整的指标"

    # 重新排序并夹紧，保证 keep < trigger < hard。
    keep = min(max(float(patch.get("keep", keep)), 0.10), 0.40)
    trigger = min(max(float(patch.get("trigger", trigger)), 0.40), 0.90)
    hard = min(max(float(patch.get("hard", hard)), 0.50), 0.98)
    if keep >= trigger:
        trigger = min(keep + 0.05, 0.90)
    if trigger >= hard:
        hard = min(trigger + 0.05, 0.98)
    patch = {
        "keep": round(keep, 3),
        "trigger": round(trigger, 3),
        "hard": round(hard, 3),
    }
    return patch, "；".join(reasons)


async def maybe_apply() -> None:
    """到达窗口且已注册 applier 时自动应用新阈值。"""
    from agent.config import CONTEXT_AUTOTUNE_ENABLED, CONTEXT_AUTOTUNE_INTERVAL

    if not CONTEXT_AUTOTUNE_ENABLED or _APPLIER is None:
        return
    if _STATS.samples - _STATS.last_adjust_sample < max(
        int(CONTEXT_AUTOTUNE_INTERVAL), 5
    ):
        return

    from agent import config as agent_config

    current = {
        "trigger": agent_config.CONTEXT_COMPACT_TRIGGER,
        "hard": agent_config.CONTEXT_COMPACT_HARD,
        "keep": agent_config.CONTEXT_COMPACT_KEEP,
    }
    patch, reason = recommend(current, _STATS.snapshot())
    _STATS.last_adjust_sample = _STATS.samples
    if not patch:
        return
    try:
        await _APPLIER(patch, reason)
        logger.info("[AutoTune] 自动应用阈值补丁 %s：%s", patch, reason)
    except Exception:
        logger.warning("[AutoTune] 自动调参失败，保留当前阈值", exc_info=True)


__all__ = ["record", "recommend", "maybe_apply", "set_applier", "snapshot"]
