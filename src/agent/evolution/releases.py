"""演进版本注册表与回滚。

版本记录只负责审计与影响范围声明；不自动改写模型权重，也不在未审批时
修改 Prompt/技能文件。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.evolution.types import (
    InterventionLayer,
    ReleaseRecord,
    ReleaseStatus,
    RiskTier,
)

logger = logging.getLogger(__name__)

RELEASE_NAMESPACE_PREFIX = ("evolution_release",)


def release_namespace(tenant_id: str) -> tuple[str, str]:
    return (*RELEASE_NAMESPACE_PREFIX, tenant_id or "default")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_release(
    store: Any,
    *,
    tenant_id: str,
    user_id: str,
    layer: InterventionLayer,
    target: str,
    artifact_version: str,
    previous_version: str,
    evidence_refs: list[str],
    impact_scope: dict[str, Any] | None = None,
    rollback_plan: str = "",
    approved_by: str = "",
    status: ReleaseStatus = ReleaseStatus.PROPOSED,
) -> ReleaseRecord | None:
    if store is None:
        return None
    record = ReleaseRecord(
        release_id=f"rel_{uuid.uuid4().hex[:16]}",
        tenant_id=tenant_id or "default",
        user_id=user_id,
        layer=layer,
        target=target,
        artifact_version=artifact_version,
        previous_version=previous_version,
        status=status,
        evidence_refs=list(evidence_refs),
        impact_scope=impact_scope or {"users": [user_id] if user_id else []},
        rollback_plan=rollback_plan or f"恢复 {previous_version}，并复核受影响 run",
        approved_by=approved_by,
        created_at=_now(),
        updated_at=_now(),
    )
    try:
        await store.aput(
            release_namespace(record.tenant_id),
            record.release_id,
            record.to_dict(),
            index=False,
        )
    except Exception:
        logger.warning("写入版本记录失败", exc_info=True)
        return None
    return record


async def get_release(store: Any, *, tenant_id: str, release_id: str) -> ReleaseRecord | None:
    if store is None:
        return None
    try:
        item = await store.aget(release_namespace(tenant_id), release_id)
    except Exception:
        return None
    return ReleaseRecord.from_dict(dict(item.value)) if item is not None else None


async def list_releases(
    store: Any,
    *,
    tenant_id: str,
    limit: int = 1000,
) -> list[ReleaseRecord]:
    if store is None:
        return []
    try:
        items = await store.asearch(release_namespace(tenant_id), limit=limit)
    except Exception:
        return []
    records = [ReleaseRecord.from_dict(dict(item.value)) for item in items]
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records


async def rollback_release(
    store: Any,
    *,
    tenant_id: str,
    release_id: str,
    actor: str = "",
) -> dict[str, Any]:
    record = await get_release(store, tenant_id=tenant_id, release_id=release_id)
    if record is None:
        return {"ok": False, "error": "RELEASE_NOT_FOUND"}
    record.status = ReleaseStatus.ROLLED_BACK
    record.approved_by = actor or record.approved_by
    record.updated_at = _now()
    if store is not None:
        try:
            await store.aput(
                release_namespace(record.tenant_id),
                record.release_id,
                record.to_dict(),
                index=False,
            )
        except Exception:
            logger.warning("回滚版本记录写入失败", exc_info=True)
            return {"ok": False, "error": "RELEASE_WRITE_FAILED"}
    return {
        "ok": True,
        "release": record.to_dict(),
        "rollback_plan": record.rollback_plan,
        "impact_scope": record.impact_scope,
        "note": "记录已回滚；模型权重或在途写入需要按影响范围人工复核。",
    }


def artifact_versions_from_manifest(manifest: Any) -> tuple[str, str]:
    """从版本快照取当前版本号；缺失时返回空串。"""
    if manifest is None:
        return "", ""
    prompt = str(getattr(manifest, "prompt_version", "") or "")
    harness = str(getattr(manifest, "harness_version", "") or "")
    return prompt or harness, harness


def risk_for_layer(layer: InterventionLayer) -> RiskTier:
    if layer in {InterventionLayer.MEMORY, InterventionLayer.EXPERIENCE}:
        return RiskTier.T0
    if layer in {
        InterventionLayer.HARNESS,
        InterventionLayer.TOOL_SCHEMA,
        InterventionLayer.TOOL_ROUTER,
        InterventionLayer.SKILL,
    }:
        return RiskTier.T1
    if layer in {InterventionLayer.MODEL_WEIGHT, InterventionLayer.ROLLBACK}:
        return RiskTier.T2
    return RiskTier.T0
