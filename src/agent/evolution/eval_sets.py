"""评估集与训练数据导出（P4 双轨的最小实现）。

不执行模型训练，只把“当前技能代际下”的成功轨迹导出为可回放数据；
跨代际旧轨迹只保留统计价值。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.evolution.types import VersionManifest
from agent.evolution.versioning import build_version_manifest
from agent.trajectory_store import list_trajectory_events

logger = logging.getLogger(__name__)

EVAL_NAMESPACE_PREFIX = ("eval_set",)


def eval_namespace(user_id: str) -> tuple[str, str]:
    return (*EVAL_NAMESPACE_PREFIX, user_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_eval_case(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    run_id: str,
    expected: str = "",
    note: str = "",
) -> dict[str, Any] | None:
    if store is None or not user_id or not thread_id:
        return None
    case = {
        "case_id": f"case_{uuid.uuid4().hex[:16]}",
        "user_id": user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "expected": expected,
        "note": note,
        "replay_tier": build_version_manifest().to_dict(),
        "created_at": _now(),
    }
    try:
        await store.aput(eval_namespace(user_id), case["case_id"], case, index=False)
    except Exception:
        logger.warning("写入评估用例失败", exc_info=True)
        return None
    return case


async def list_eval_cases(store: Any, *, user_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    if store is None or not user_id:
        return []
    try:
        items = await store.asearch(eval_namespace(user_id), limit=limit)
    except Exception:
        return []
    cases = [dict(item.value) for item in items]
    cases.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return cases


async def export_training_data(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    skill_generation: int | None = None,
) -> dict[str, Any]:
    """导出当前技能代际下的成功轨迹；不按跨代际旧数据训练。"""
    manifest = build_version_manifest()
    target_generation = (
        int(skill_generation)
        if skill_generation is not None
        else int(manifest.skill_generation)
    )
    events = await list_trajectory_events(store, user_id=user_id, thread_id=thread_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("run_id", "")), []).append(event)

    selected: list[dict[str, Any]] = []
    for run_events in grouped.values():
        failed = any(str(event.get("status")) == "error" for event in run_events)
        if failed:
            continue
        for event in run_events:
            metadata = (event.get("atdp") or {}).get("m_t") or {}
            if int(metadata.get("skill_generation") or 0) != target_generation:
                continue
            selected.append(event)
    return {
        "user_id": user_id,
        "thread_id": thread_id,
        "skill_generation": target_generation,
        "event_count": len(selected),
        "events": selected,
        "note": "只导出当前技能代际的未失败轨迹；训练本身由外部训练服务执行。",
    }


class WeightUpdatePort:
    """权重更新端口：本仓库不执行训练，只保留外部训练服务的接入点。"""

    async def submit(self, dataset_ref: str, manifest: VersionManifest) -> str:
        raise NotImplementedError(
            "本仓库不执行模型权重训练；请将 dataset_ref 交给外部训练服务。"
        )
