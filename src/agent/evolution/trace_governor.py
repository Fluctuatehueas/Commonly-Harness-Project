"""轨迹治理：在不改变原有轨迹字段的前提下补齐 ATDP v2 与脱敏。

治理发生在 ``TrajectoryMiddleware`` 写 Store 之前。任何异常都由调用方
兜底，治理失败不能阻断 Agent 主流程。
"""

from __future__ import annotations

import json
from typing import Any

from agent.config import (
    EVOLUTION_ENABLED,
    EVOLUTION_KEEP_REASONING,
    EVOLUTION_REDACTION_ENABLED,
    EVOLUTION_REPLAY_TIER,
    EVOLUTION_TRAJECTORY_ENABLED,
    TRAJECTORY_MAX_TEXT,
)
from agent.evolution.redaction import redact_mapping, redact_text
from agent.evolution.types import ReplayTier, RiskTier, VersionManifest
from agent.evolution.versioning import build_version_manifest


def _truncate(value: Any, limit: int = TRAJECTORY_MAX_TEXT) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[已截断，共 {len(text)} 字符]"


def _risk_tier_for_event(event_type: str, tool_name: str) -> RiskTier:
    if event_type != "tool":
        return RiskTier.T0
    low_risk_tools = {
        "read_file",
        "search_web",
        "fetch_web",
        "read_word_document",
        "knowledge_search",
    }
    high_risk_tools = {
        "write_file",
        "edit_file",
        "host_write_file",
        "host_execute",
        "publish_to_project",
        "run_in_sandbox",
    }
    if tool_name in high_risk_tools:
        return RiskTier.T2
    if tool_name in low_risk_tools:
        return RiskTier.T1
    return RiskTier.T1


def _observable(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": event.get("type", ""),
        "summary": _truncate(event.get("preview") or event.get("result") or "", 600),
        "raw_ref": f"trajectory/{event.get('id', '')}",
    }


def _hidden_state(event: dict[str, Any]) -> dict[str, Any]:
    reasoning = event.get("result", "") if event.get("type") == "model" else ""
    return {
        "plan_digest": _truncate(reasoning, 200) if reasoning else "",
        "confidence": None,
        "reasoning_retained": bool(EVOLUTION_KEEP_REASONING and reasoning),
    }


def _action(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "tool_call" if event.get("type") in {"tool", "skill", "delegation"} else "model_call",
        "tool": event.get("tool_name", ""),
        "args": event.get("args", ""),
        "tool_call_id": event.get("tool_call_id", ""),
        "schema_version": "",
    }


def _outcome(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "returned": event.get("status") not in {"error", "interrupted"},
        "trust": "unknown",
        "status": event.get("status", ""),
    }


def build_atdp_payload(
    event: dict[str, Any],
    *,
    manifest: VersionManifest,
    risk_tier: RiskTier,
) -> dict[str, Any]:
    metadata = manifest.to_dict()
    metadata.update(
        {
            "user_id": event.get("atdp_meta_user_id", ""),
            "tenant_id": event.get("atdp_meta_tenant_id", ""),
            "thread_id": event.get("atdp_meta_thread_id", ""),
            "run_id": event.get("run_id", ""),
            "tokens": event.get("tokens", {}),
            "duration_ms": event.get("duration_ms"),
            "graph": event.get("graph", "main"),
        }
    )
    metadata.pop("atdp_meta_user_id", None)
    metadata.pop("atdp_meta_tenant_id", None)
    metadata.pop("atdp_meta_thread_id", None)
    action = _action(event)
    action["schema_version"] = manifest.tool_schema_version
    return {
        "o_t": _observable(event),
        "h_t": _hidden_state(event),
        "a_t": action,
        "y_t": _outcome(event),
        "r_t": {"value": None, "source": "pending", "evidence_ref": ""},
        "m_t": metadata,
        "risk_tier": risk_tier.value,
    }


def govern_event(
    event: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """补齐 ATDP v2、脱敏并返回可落库事件。"""
    if not EVOLUTION_ENABLED or not EVOLUTION_TRAJECTORY_ENABLED:
        return event

    event["schema_version"] = "atdp.v2"
    event["replay_tier"] = EVOLUTION_REPLAY_TIER
    event["atdp_meta_user_id"] = user_id
    event["atdp_meta_tenant_id"] = tenant_id
    event["atdp_meta_thread_id"] = thread_id

    risk_tier = _risk_tier_for_event(
        str(event.get("type", "")),
        str(event.get("tool_name", "")),
    )
    manifest = build_version_manifest()
    event["atdp"] = build_atdp_payload(event, manifest=manifest, risk_tier=risk_tier)

    if EVOLUTION_REDACTION_ENABLED:
        redaction_rules: list[str] = []
        for key in ("preview", "args", "result"):
            cleaned, rules = redact_mapping(event.get(key, ""))
            event[key] = cleaned
            redaction_rules.extend(rules)
        if redaction_rules:
            event["redaction"] = {
                "applied": True,
                "rules": sorted(set(redaction_rules)),
            }
        else:
            event["redaction"] = {"applied": True, "rules": []}
    else:
        event["redaction"] = {"applied": False, "rules": []}

    # 防止 ATDP 字段本身把单行事件撑大。
    try:
        encoded = json.dumps(event.get("atdp", {}), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = ""
    if len(encoded) > TRAJECTORY_MAX_TEXT:
        event["atdp"]["o_t"]["summary"] = _truncate(
            event["atdp"]["o_t"].get("summary", ""),
            max(TRAJECTORY_MAX_TEXT // 3, 1),
        )
        event["atdp"]["h_t"]["plan_digest"] = ""
        event["atdp"]["a_t"]["args"] = ""
    for key in ("atdp_meta_user_id", "atdp_meta_tenant_id", "atdp_meta_thread_id"):
        event.pop(key, None)
    return event


def replay_tier_value() -> ReplayTier:
    try:
        return ReplayTier(EVOLUTION_REPLAY_TIER)
    except ValueError:
        return ReplayTier.APPROX
