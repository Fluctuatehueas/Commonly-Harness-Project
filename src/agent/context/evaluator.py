"""压缩评估（v1 规则版）。

评估目标不是“压得多不多”，而是压缩后任务是否还成立。v1 只做确定性
计算，不调用模型，因此可以在压缩结束时同步完成，成本近似为零：

- 保真度：锚点在摘要中的保留比例；
- 时机：硬水位强制 / 有过延迟 / 正常；
- 资源：压缩前后 token、摘要消耗、耗时；
- 回溯成本：v1 先留 0，v1.5 由轨迹侧统计归档文件读取。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.constants import TAG_NOSTREAM

from agent.context.anchors import Anchor, anchor_fidelity

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """你在评估一次上下文压缩的时机是否合适。

压缩前占用：__BEFORE__ tokens；压缩后：__AFTER__ tokens；窗口：__WINDOW__ tokens。
触发方式：__TRIGGER__；此前延迟次数：__DEFERRED__。

压缩后的结构化摘要：
<summary>
__SUMMARY__
</summary>

判断标准：
- good：压缩发生在阶段收敛后，摘要保留了继续任务所需的信息；
- early：任务尚在推导中，压缩过早，可能丢失中间结果；
- late：占用已接近窗口上限才压缩，风险偏高。

只返回 JSON：{"timing": "good|early|late", "reason": "不超过 60 字"}
"""


async def judge_compaction_timing(
    model: BaseChatModel,
    *,
    summary: str,
    trigger_type: str,
    deferred: int,
    before_tokens: int,
    after_tokens: int,
    window: int,
) -> dict[str, Any] | None:
    """LLM 时机裁判；调用失败返回 None，由调用方保留规则标签。"""
    prompt = (
        _JUDGE_PROMPT.replace("__BEFORE__", str(before_tokens))
        .replace("__AFTER__", str(after_tokens))
        .replace("__WINDOW__", str(window))
        .replace("__TRIGGER__", trigger_type)
        .replace("__DEFERRED__", str(deferred))
        .replace("__SUMMARY__", summary[:3000])
    )
    try:
        response = await model.ainvoke(prompt, config={"tags": [TAG_NOSTREAM]})
        content = getattr(response, "content", "") or ""
        start = str(content).find("{")
        end = str(content).rfind("}")
        if start == -1 or end <= start:
            return None
        payload = json.loads(str(content)[start : end + 1])
        timing = str(payload.get("timing") or "").strip()
        if timing not in {"good", "early", "late"}:
            return None
        return {
            "timing": timing,
            "reason": str(payload.get("reason") or "")[:120],
        }
    except Exception:
        logger.warning("LLM 时机裁判失败，保留规则标签", exc_info=True)
        return None


def evaluate_compaction(
    *,
    anchors: list[Anchor],
    summary: str,
    before_tokens: int,
    after_tokens: int,
    summary_tokens: int,
    trigger_type: str,
    deferred: int,
    duration_ms: int,
) -> dict[str, Any]:
    """返回可直接写入轨迹回执的评估结果。"""
    kept, total = anchor_fidelity(summary, anchors)
    fidelity = round(kept / total, 4) if total else 1.0

    if trigger_type == "hard":
        timing = "forced"
    elif deferred > 0:
        timing = "deferred"
    elif trigger_type == "manual":
        timing = "manual"
    else:
        timing = "good"

    return {
        "status": "done",
        "fidelity": fidelity,
        "anchors_total": total,
        "anchors_kept": kept,
        "timing": timing,
        "retrieval_cost": {"reads": 0, "tokens": 0},
        "reason": (
            f"{trigger_type} 触发，延迟 {deferred} 次；"
            f"锚点 {kept}/{total}；节省 {max(before_tokens - after_tokens, 0)} tokens"
        ),
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": max(before_tokens - after_tokens, 0),
        "summary_tokens": summary_tokens,
        "duration_ms": duration_ms,
    }
