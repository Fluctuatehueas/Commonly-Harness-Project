"""演进控制面：规则归因 -> 候选 -> 审批/版本记录。

控制面不自主设定改进目标，也不执行模型权重训练。低风险经验可自动准入；
Prompt/技能/工具 Schema 只生成候选与版本记录，必须人工批准。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.config import (
    EVOLUTION_CONTROL_MIN_SAMPLES,
    EVOLUTION_MODE,
    EVOLUTION_REQUIRE_HUMAN_APPROVAL,
)
from agent.evolution.experience import ExperienceStore
from agent.evolution.releases import create_release, risk_for_layer
from agent.evolution.types import (
    AdmissionState,
    Attribution,
    InterventionCandidate,
    InterventionLayer,
    ReleaseStatus,
    RiskTier,
)

logger = logging.getLogger(__name__)

CANDIDATE_NAMESPACE_PREFIX = ("evolution_candidate",)


def candidate_namespace(user_id: str) -> tuple[str, str]:
    return (*CANDIDATE_NAMESPACE_PREFIX, user_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def propose_candidate(
    store: Any,
    *,
    user_id: str,
    tenant_id: str,
    attribution: Attribution,
    failure_count: int,
) -> InterventionCandidate | None:
    if store is None or not user_id:
        return None
    if EVOLUTION_MODE == "observe":
        return None
    if attribution.high_uncertainty or attribution.layer == InterventionLayer.NOOP:
        return None
    if failure_count < EVOLUTION_CONTROL_MIN_SAMPLES:
        return None

    risk = risk_for_layer(attribution.layer)
    candidate = InterventionCandidate(
        candidate_id=f"cand_{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        tenant_id=tenant_id,
        layer=attribution.layer,
        target=attribution.target,
        change_spec={
            "signal": attribution.signal,
            "reason": attribution.reason,
            "failure_count": failure_count,
        },
        confidence=attribution.confidence,
        evidence_refs=list(attribution.evidence_refs),
        risk_tier=risk,
        requires_human_approval=(
            EVOLUTION_REQUIRE_HUMAN_APPROVAL and risk != RiskTier.T0
        ),
        status="proposed",
        created_at=_now(),
    )
    try:
        await store.aput(
            candidate_namespace(user_id),
            candidate.candidate_id,
            candidate.to_dict(),
            index=False,
        )
    except Exception:
        logger.warning("写入干预候选失败", exc_info=True)
        return None
    return candidate


async def list_candidates(
    store: Any,
    *,
    user_id: str,
    limit: int = 1000,
) -> list[InterventionCandidate]:
    if store is None or not user_id:
        return []
    try:
        items = await store.asearch(candidate_namespace(user_id), limit=limit)
    except Exception:
        return []
    records = [InterventionCandidate.from_dict(dict(item.value)) for item in items]
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records


async def get_candidate(
    store: Any,
    *,
    user_id: str,
    candidate_id: str,
) -> InterventionCandidate | None:
    if store is None or not user_id:
        return None
    try:
        item = await store.aget(candidate_namespace(user_id), candidate_id)
    except Exception:
        return None
    return (
        InterventionCandidate.from_dict(dict(item.value))
        if item is not None
        else None
    )


async def apply_candidate(
    store: Any,
    *,
    user_id: str,
    tenant_id: str,
    candidate_id: str,
    approved_by: str = "",
    manifest: Any = None,
) -> dict[str, Any]:
    candidate = await get_candidate(store, user_id=user_id, candidate_id=candidate_id)
    if candidate is None:
        return {"ok": False, "error": "CANDIDATE_NOT_FOUND"}

    risk = candidate.risk_tier
    if candidate.requires_human_approval and not approved_by:
        return {"ok": False, "error": "HUMAN_APPROVAL_REQUIRED"}

    if candidate.layer == InterventionLayer.EXPERIENCE:
        experience_id = str(candidate.change_spec.get("experience_id") or "")
        if experience_id:
            await ExperienceStore(store, user_id).set_state(
                experience_id,
                AdmissionState.ACTIVE,
            )
        candidate.status = "applied"
        await _save_candidate(store, candidate)
        return {"ok": True, "candidate": candidate.to_dict(), "effect": "experience_activated"}

    if candidate.layer == InterventionLayer.MODEL_WEIGHT:
        return {
            "ok": False,
            "error": "MODEL_WEIGHT_NOT_SUPPORTED",
            "note": "本仓库不执行权重训练，仅保留候选与数据导出接口。",
        }

    prompt_version = str(getattr(manifest, "prompt_version", "") or "unknown")
    harness_version = str(getattr(manifest, "harness_version", "") or "unknown")
    release = await create_release(
        store,
        tenant_id=tenant_id,
        user_id=user_id,
        layer=candidate.layer,
        target=candidate.target,
        artifact_version=f"{prompt_version}_{uuid.uuid4().hex[:6]}",
        previous_version=prompt_version or harness_version,
        evidence_refs=candidate.evidence_refs,
        impact_scope={"users": [user_id], "tenant_id": tenant_id},
        approved_by=approved_by,
        status=(
            ReleaseStatus.APPROVED
            if approved_by
            else ReleaseStatus.PROPOSED
        ),
    )
    candidate.status = "applied"
    await _save_candidate(store, candidate)
    return {
        "ok": True,
        "candidate": candidate.to_dict(),
        "release": release.to_dict() if release else None,
        "note": "已生成版本记录；Prompt/技能/工具 Schema 的实际变更需由发布流程执行。",
    }


async def reject_candidate(
    store: Any,
    *,
    user_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    candidate = await get_candidate(store, user_id=user_id, candidate_id=candidate_id)
    if candidate is None:
        return {"ok": False, "error": "CANDIDATE_NOT_FOUND"}
    candidate.status = "rejected"
    await _save_candidate(store, candidate)
    return {"ok": True, "candidate": candidate.to_dict()}


async def _save_candidate(store: Any, candidate: InterventionCandidate) -> None:
    if store is None:
        return
    try:
        await store.aput(
            candidate_namespace(candidate.user_id),
            candidate.candidate_id,
            candidate.to_dict(),
            index=False,
        )
    except Exception:
        logger.warning("更新干预候选失败", exc_info=True)
