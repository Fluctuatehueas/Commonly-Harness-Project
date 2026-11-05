"""演进模块 HTTP 接口。

本期前端只使用 ``GET /evolution/experiences``；其余接口供内部规则引擎、
运维与后续发布流程使用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.config import (
    EVOLUTION_ATTRIBUTION_ENABLED,
    EVOLUTION_CONTROL_PLANE_ENABLED,
    EVOLUTION_ENABLED,
    EVOLUTION_EXPERIENCE_ENABLED,
    EVOLUTION_JUDGE_ENABLED,
    EVOLUTION_MODE,
    EVOLUTION_REPLAY_TIER,
    EVOLUTION_REQUIRE_HUMAN_APPROVAL,
    EVOLUTION_TENANT_ID,
    EVOLUTION_TOOL_HEALTH_ENABLED,
)
from agent.evolution.control_plane import (
    apply_candidate,
    list_candidates,
    reject_candidate,
)
from agent.evolution.eval_sets import (
    create_eval_case,
    export_training_data,
    list_eval_cases,
)
from agent.evolution.experience import ExperienceStore
from agent.evolution.releases import list_releases, rollback_release
from agent.evolution.reward import append_reward, list_rewards
from agent.evolution.tool_health import list_tool_health
from agent.evolution.types import AdmissionState
from agent.evolution.versioning import build_version_manifest
from api.agent_loader import agent_loader


router = APIRouter(prefix="/evolution", tags=["evolution"])


def _require_store() -> Any:
    store = agent_loader.store
    if store is None:
        raise HTTPException(status_code=503, detail="Agent Store 尚未初始化")
    return store


def _tenant(tenant_id: str | None = None) -> str:
    return tenant_id or EVOLUTION_TENANT_ID or "default"


@router.get("/config")
async def get_config() -> dict[str, Any]:
    return {
        "enabled": EVOLUTION_ENABLED,
        "mode": EVOLUTION_MODE,
        "replay_tier": EVOLUTION_REPLAY_TIER,
        "require_human_approval": EVOLUTION_REQUIRE_HUMAN_APPROVAL,
        "modules": {
            "experience": EVOLUTION_EXPERIENCE_ENABLED,
            "judge": EVOLUTION_JUDGE_ENABLED,
            "attribution": EVOLUTION_ATTRIBUTION_ENABLED,
            "control_plane": EVOLUTION_CONTROL_PLANE_ENABLED,
            "tool_health": EVOLUTION_TOOL_HEALTH_ENABLED,
        },
        "version_manifest": build_version_manifest().to_dict(),
    }


@router.get("/experiences")
async def get_experiences(
    user_id: str = Query("anonymous", min_length=1),
    status: str | None = Query(None, description="逗号分隔的准入状态"),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    store = _require_store()
    statuses: set[AdmissionState] | None = None
    if status:
        try:
            statuses = {
                AdmissionState(item.strip())
                for item in status.split(",")
                if item.strip()
            }
        except ValueError:
            raise HTTPException(status_code=422, detail="无效的经验状态")
    records = await ExperienceStore(store, user_id).list(statuses=statuses, limit=limit)
    return {
        "user_id": user_id,
        "count": len(records),
        "experiences": [record.to_dict() for record in records],
    }


@router.get("/experiences/{experience_id}")
async def get_experience(
    experience_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    record = await ExperienceStore(_require_store(), user_id).get(experience_id)
    if record is None:
        raise HTTPException(status_code=404, detail="经验不存在")
    return record.to_dict()


class ExperienceStateRequest(BaseModel):
    user_id: str = Field("anonymous", min_length=1)


@router.post("/experiences/{experience_id}/approve")
async def approve_experience(
    experience_id: str,
    request: ExperienceStateRequest,
) -> dict[str, Any]:
    record = await ExperienceStore(_require_store(), request.user_id).set_state(
        experience_id,
        AdmissionState.ACTIVE,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="经验不存在")
    return {"ok": True, "experience": record.to_dict()}


@router.post("/experiences/{experience_id}/quarantine")
async def quarantine_experience(
    experience_id: str,
    request: ExperienceStateRequest,
) -> dict[str, Any]:
    record = await ExperienceStore(_require_store(), request.user_id).set_state(
        experience_id,
        AdmissionState.QUARANTINED,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="经验不存在")
    return {"ok": True, "experience": record.to_dict()}


@router.delete("/experiences/{experience_id}")
async def delete_experience(
    experience_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    await ExperienceStore(_require_store(), user_id).delete(experience_id)
    return {"ok": True}


@router.get("/tool-health")
async def get_tool_health(
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    records = await list_tool_health(_require_store(), user_id=user_id)
    return {"user_id": user_id, "count": len(records), "tools": records}


class RewardRequest(BaseModel):
    user_id: str = Field("anonymous", min_length=1)
    thread_id: str = Field(..., min_length=1)
    tenant_id: str = ""
    execution_id: str = ""
    event_id: str = ""
    source: str = "external"
    value: float
    category: str = "external"
    confidence: float = 0.9
    evidence: str = ""


@router.post("/rewards")
async def post_reward(request: RewardRequest) -> dict[str, Any]:
    record = await append_reward(
        _require_store(),
        user_id=request.user_id,
        thread_id=request.thread_id,
        tenant_id=_tenant(request.tenant_id),
        execution_id=request.execution_id or request.thread_id,
        event_id=request.event_id or request.execution_id or request.thread_id,
        source=request.source,
        value=request.value,
        category=request.category,
        confidence=request.confidence,
        evidence=request.evidence,
        delayed=True,
    )
    if record is None:
        raise HTTPException(status_code=400, detail="奖励未写入")
    return {"ok": True, "reward": record.to_dict()}


@router.get("/rewards")
async def get_rewards(
    user_id: str = Query("anonymous", min_length=1),
    thread_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    records = await list_rewards(_require_store(), user_id=user_id, thread_id=thread_id)
    return {"count": len(records), "rewards": [record.to_dict() for record in records]}


@router.get("/candidates")
async def get_candidates(
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    records = await list_candidates(_require_store(), user_id=user_id)
    return {"count": len(records), "candidates": [record.to_dict() for record in records]}


class CandidateApplyRequest(BaseModel):
    user_id: str = Field("anonymous", min_length=1)
    tenant_id: str = ""
    approved_by: str = ""


@router.post("/candidates/{candidate_id}/apply")
async def post_apply_candidate(
    candidate_id: str,
    request: CandidateApplyRequest,
) -> dict[str, Any]:
    result = await apply_candidate(
        _require_store(),
        user_id=request.user_id,
        tenant_id=_tenant(request.tenant_id),
        candidate_id=candidate_id,
        approved_by=request.approved_by,
        manifest=build_version_manifest(),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/candidates/{candidate_id}/reject")
async def post_reject_candidate(
    candidate_id: str,
    request: CandidateApplyRequest,
) -> dict[str, Any]:
    result = await reject_candidate(
        _require_store(),
        user_id=request.user_id,
        candidate_id=candidate_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.get("/releases")
async def get_releases(
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    records = await list_releases(_require_store(), tenant_id=_tenant(tenant_id))
    return {
        "tenant_id": _tenant(tenant_id),
        "count": len(records),
        "releases": [record.to_dict() for record in records],
    }


class RollbackRequest(BaseModel):
    tenant_id: str = ""
    actor: str = ""


@router.post("/releases/{release_id}/rollback")
async def post_rollback(
    release_id: str,
    request: RollbackRequest,
) -> dict[str, Any]:
    result = await rollback_release(
        _require_store(),
        tenant_id=_tenant(request.tenant_id),
        release_id=release_id,
        actor=request.actor,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.get("/eval-sets")
async def get_eval_sets(
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    cases = await list_eval_cases(_require_store(), user_id=user_id)
    return {"count": len(cases), "cases": cases}


class EvalCaseRequest(BaseModel):
    user_id: str = Field("anonymous", min_length=1)
    thread_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    expected: str = ""
    note: str = ""


@router.post("/eval-sets")
async def post_eval_set(request: EvalCaseRequest) -> dict[str, Any]:
    case = await create_eval_case(
        _require_store(),
        user_id=request.user_id,
        thread_id=request.thread_id,
        run_id=request.run_id,
        expected=request.expected,
        note=request.note,
    )
    if case is None:
        raise HTTPException(status_code=400, detail="评估用例未写入")
    return {"ok": True, "case": case}


@router.get("/training-data")
async def get_training_data(
    user_id: str = Query("anonymous", min_length=1),
    thread_id: str = Query(..., min_length=1),
    skill_generation: int | None = Query(None, ge=0),
) -> dict[str, Any]:
    return await export_training_data(
        _require_store(),
        user_id=user_id,
        thread_id=thread_id,
        skill_generation=skill_generation,
    )


@router.get("/metrics")
async def get_metrics(
    user_id: str = Query("anonymous", min_length=1),
    thread_id: str = Query("", description="可选：同时统计该会话奖励"),
) -> dict[str, Any]:
    store = _require_store()
    experiences = await ExperienceStore(store, user_id).list(limit=2000)
    tool_health = await list_tool_health(store, user_id=user_id)
    candidates = await list_candidates(store, user_id=user_id)
    rewards = (
        await list_rewards(store, user_id=user_id, thread_id=thread_id)
        if thread_id
        else []
    )
    states = {state.value: 0 for state in AdmissionState}
    for record in experiences:
        states[record.admission_state.value] = states.get(record.admission_state.value, 0) + 1
    return {
        "user_id": user_id,
        "experiences": {"total": len(experiences), "by_state": states},
        "tool_health": {"tools": len(tool_health)},
        "rewards": {
            "count": len(rewards),
            "sum": round(sum(record.value for record in rewards), 4),
        },
        "candidates": {"total": len(candidates)},
    }
