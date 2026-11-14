"""影子评估：计算档位并落 JSONL，不改变任何执行行为。

任何异常都被吞掉——调度模块不可用时不能影响主流程。
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from .config import get_config
from .features import extract_features
from .models import ShadowRecord
from .tiering import decide_tier, suggest_theta

logger = logging.getLogger(__name__)

_WRITE_LOCK = asyncio.Lock()


def _preview(message: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return (message or "").replace("\n", " ")[:limit]


async def shadow_evaluate(
    message: str,
    user_id: str,
    decision: Any = None,
    *,
    prev_tier: Any = None,
) -> Optional[ShadowRecord]:
    """入口：开关关闭时立即返回；否则计算、落盘、返回记录。"""
    try:
        cfg = get_config()
        if not cfg.enabled or not cfg.shadow:
            return None

        flow = getattr(decision, "flow", None) if decision is not None else None
        plan = getattr(decision, "plan", None) if decision is not None else None

        features = extract_features(message, flow=flow, plan=plan)
        tier_decision = decide_tier(features, cfg, prev_tier=prev_tier)

        text = message or ""
        raw_tier = max(tier_decision.probs, key=tier_decision.probs.get)
        record = ShadowRecord(
            run_id=f"shadow-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}",
            user_id=user_id,
            mode="reference" if flow is not None else "dynamic",
            flow_id=getattr(flow, "id", None) if flow is not None else None,
            raw_tier=raw_tier,
            tier=tier_decision.tier,
            upgraded=tier_decision.upgraded,
            reason=tier_decision.reason,
            s=tier_decision.s,
            probs=tier_decision.probs,
            features=features,
            message_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            message_len=len(text),
            message_preview=_preview(text, cfg.preview_chars),
        )
        await _append(cfg.shadow_path, record)
        return record
    except Exception:
        logger.exception("调度影子评估失败，已忽略")
        return None


async def _append(path: Path, record: ShadowRecord) -> None:
    line = record.model_dump_json()
    async with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def summarize_shadow(path: Path) -> dict[str, Any]:
    """影子数据摘要：档位分布 + 按先验分位的建议边界。

    raw_tier_counts 反映纯概率分档；tier_counts 反映叠加安全规则后的最终结果。
    两者差异即滞回 / 低置信度升档 / 缺失兜底的实际影响面。
    """
    rows = read_records(path)
    scores = [float(r["s"]) for r in rows if "s" in r]
    counts: dict[str, int] = {"EZ": 0, "MD": 0, "HD": 0}
    raw_counts: dict[str, int] = {"EZ": 0, "MD": 0, "HD": 0}
    blocked_ez = 0
    for row in rows:
        tier = str(row.get("tier", ""))
        raw = str(row.get("raw_tier", ""))
        if tier in counts:
            counts[tier] += 1
        if raw in raw_counts:
            raw_counts[raw] += 1
        if raw == "EZ" and tier != "EZ":
            blocked_ez += 1
    theta1, theta2 = suggest_theta(scores)
    return {
        "records": len(rows),
        "tier_counts": counts,
        "raw_tier_counts": raw_counts,
        "ez_blocked": blocked_ez,
        "s_mean": round(sum(scores) / len(scores), 4) if scores else None,
        "s_p327": round(theta1, 4),
        "s_p840": round(theta2, 4),
    }
