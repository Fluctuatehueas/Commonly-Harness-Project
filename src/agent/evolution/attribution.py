"""失败归因：规则映射 + 行动者/观察者双视角一致性检查。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.constants import TAG_NOSTREAM

from agent.config import EVOLUTION_ATTRIBUTION_ENABLED
from agent.evolution.types import Attribution, InterventionLayer, RunSignals

logger = logging.getLogger(__name__)

ATTRIBUTION_NAMESPACE_PREFIX = ("attribution",)

RULE_TABLE: dict[str, InterventionLayer] = {
    "knowledge_gap": InterventionLayer.EXPERIENCE,
    "repeated_failure": InterventionLayer.HARNESS,
    "tool_error": InterventionLayer.TOOL_SCHEMA,
    "verification_failed": InterventionLayer.HARNESS,
    "hitl_rejected": InterventionLayer.HARNESS,
    "user_correction": InterventionLayer.EXPERIENCE,
    "multi_task_failure": InterventionLayer.MODEL_WEIGHT,
}

_ACTOR_PROMPT = """You are the agent that just failed. Explain the failure.

Return only JSON:
{"layer": "memory|experience|harness|tool_schema|tool_router|skill|model_weight|noop",
 "target": "short target", "reason": "...", "confidence": 0.0-1.0}

Signals:
__SIGNALS__
"""

_OBSERVER_PROMPT = """You are an independent auditor reviewing another agent's failure.

Return only JSON:
{"layer": "memory|experience|harness|tool_schema|tool_router|skill|model_weight|noop",
 "target": "short target", "reason": "...", "confidence": 0.0-1.0}

Signals:
__SIGNALS__
"""


def attribution_namespace(user_id: str, thread_id: str) -> tuple[str, str, str]:
    return (*ATTRIBUTION_NAMESPACE_PREFIX, user_id, thread_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _parse_payload(content: str) -> dict[str, Any]:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        payload = json.loads(content[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _layer(value: Any, fallback: InterventionLayer) -> InterventionLayer:
    try:
        return InterventionLayer(str(value))
    except ValueError:
        return fallback


def rule_based_layer(signals: RunSignals) -> tuple[InterventionLayer, str]:
    if signals.verification_failed or signals.hitl_rejected:
        return InterventionLayer.HARNESS, "verification_or_hitl_failure"
    if signals.tool_errors:
        return InterventionLayer.TOOL_SCHEMA, "tool_error"
    if signals.repeated_steps >= 2:
        return InterventionLayer.HARNESS, "repeated_failure"
    if signals.user_correction:
        return InterventionLayer.EXPERIENCE, "user_correction"
    return InterventionLayer.NOOP, "insufficient_evidence"


def _signal_payload(signals: RunSignals) -> str:
    return json.dumps(
        {
            "tool_errors": signals.tool_errors[-5:],
            "verification_failed": signals.verification_failed,
            "hitl_rejected": signals.hitl_rejected,
            "user_correction": signals.user_correction,
            "repeated_steps": signals.repeated_steps,
        },
        ensure_ascii=False,
    )


async def _ask_layer(model: BaseChatModel, prompt: str, fallback: InterventionLayer) -> tuple[InterventionLayer, str, float]:
    try:
        # 内部归因输出不进入消息流。
        response = await model.ainvoke(prompt, config={"tags": [TAG_NOSTREAM]})
        payload = _parse_payload(_message_text(response))
        return (
            _layer(payload.get("layer"), fallback),
            str(payload.get("target") or ""),
            float(payload.get("confidence") or 0.5),
        )
    except Exception:
        logger.warning("归因模型调用失败", exc_info=True)
        return fallback, "", 0.3


async def attribute_failure(
    model: BaseChatModel,
    *,
    user_id: str,
    thread_id: str,
    execution_id: str,
    signals: RunSignals,
    evidence_refs: list[str],
) -> Attribution:
    """对失败 run 做规则归因与双视角检查；不一致时标记高不确定性。"""
    fallback_layer, signal = rule_based_layer(signals)
    if not EVOLUTION_ATTRIBUTION_ENABLED or fallback_layer == InterventionLayer.NOOP:
        return Attribution(
            attribution_id=f"att_{uuid.uuid4().hex[:16]}",
            execution_id=execution_id,
            signal=signal,
            layer=InterventionLayer.NOOP,
            target="",
            reason="证据不足",
            confidence=0.2,
            high_uncertainty=True,
            evidence_refs=list(evidence_refs),
            actor_layer="",
            observer_layer="",
            created_at=_now(),
        )

    payload = _signal_payload(signals)
    actor_layer, actor_target, actor_confidence = await _ask_layer(
        model,
        _ACTOR_PROMPT.replace("__SIGNALS__", payload),
        fallback_layer,
    )
    observer_layer, observer_target, observer_confidence = await _ask_layer(
        model,
        _OBSERVER_PROMPT.replace("__SIGNALS__", payload),
        fallback_layer,
    )
    high_uncertainty = actor_layer != observer_layer
    layer = fallback_layer if high_uncertainty else actor_layer
    target = observer_target or actor_target
    confidence = (
        min(actor_confidence, observer_confidence, 0.4)
        if high_uncertainty
        else max(min(actor_confidence, observer_confidence), 0.5)
    )
    reason = "双视角归因不一致" if high_uncertainty else "双视角归因一致"
    return Attribution(
        attribution_id=f"att_{uuid.uuid4().hex[:16]}",
        execution_id=execution_id,
        signal=signal,
        layer=layer,
        target=target[:200],
        reason=reason,
        confidence=round(confidence, 4),
        high_uncertainty=high_uncertainty,
        evidence_refs=list(evidence_refs),
        actor_layer=actor_layer.value,
        observer_layer=observer_layer.value,
        created_at=_now(),
    )


async def save_attribution(store: Any, user_id: str, thread_id: str, attribution: Attribution) -> None:
    if store is None or not user_id or not thread_id:
        return
    try:
        await store.aput(
            attribution_namespace(user_id, thread_id),
            attribution.attribution_id,
            attribution.to_dict(),
            index=False,
        )
    except Exception:
        logger.warning("写入归因结果失败", exc_info=True)


async def list_attributions(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    limit: int = 1000,
) -> list[Attribution]:
    if store is None or not user_id or not thread_id:
        return []
    try:
        items = await store.asearch(
            attribution_namespace(user_id, thread_id),
            limit=limit,
        )
    except Exception:
        return []
    records = [Attribution.from_dict(dict(item.value)) for item in items]
    records.sort(key=lambda item: item.created_at)
    return records
