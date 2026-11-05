"""奖励账本：把工具结果、HITL 拒绝、用户纠正等信号落成可聚合记录。

奖励账本只追加，不改写历史轨迹事件；延迟奖励通过同一账本回填。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.config import EVOLUTION_REWARD_ENABLED
from agent.evolution.types import RewardRecord

logger = logging.getLogger(__name__)

REWARD_NAMESPACE_PREFIX = ("reward",)
LIST_LIMIT = 5000


def reward_namespace(user_id: str, thread_id: str) -> tuple[str, str, str]:
    return (*REWARD_NAMESPACE_PREFIX, user_id, thread_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_error_category(text: str) -> str:
    """从 MCP 错误 JSON 中读取 category；解析失败返回空串。"""
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return ""
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("category") or "")
    return ""


def classify_tool_outcome(
    *,
    status: str,
    result_text: str,
    error: BaseException | None = None,
) -> tuple[float, str]:
    """把一次工具调用映射为 (奖励值, 类别)。

    infra 失败不计入工具惩罚，只计入服务可用性。
    """
    if error is not None:
        name = type(error).__name__.lower()
        text = f"{name} {error}".lower()
        if any(token in text for token in ("timeout", "connect", "unavailable", "503", "502")):
            return -0.1, "infra_error"
        return -0.3, "tool_error"

    if status == "error":
        category = parse_error_category(result_text)
        if category in {"timeout", "transient", "auth", "config", "unavailable", "circuit_open"}:
            return -0.1, "infra_error"
        return -0.3, "tool_error"

    if status == "interrupted":
        return 0.0, "interrupted"

    return 0.2, "success"


async def append_reward(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    tenant_id: str,
    execution_id: str,
    event_id: str,
    source: str,
    value: float,
    category: str,
    confidence: float = 0.8,
    evidence: str = "",
    delayed: bool = False,
) -> RewardRecord | None:
    if not EVOLUTION_REWARD_ENABLED or store is None or not user_id or not thread_id:
        return None
    record = RewardRecord(
        reward_id=f"rw_{uuid.uuid4().hex[:16]}",
        execution_id=execution_id,
        event_id=event_id,
        thread_id=thread_id,
        user_id=user_id,
        tenant_id=tenant_id,
        source=source,
        value=float(value),
        confidence=float(confidence),
        category=category,
        evidence=evidence[:2000],
        delayed=delayed,
        created_at=_now(),
    )
    try:
        await store.aput(
            reward_namespace(user_id, thread_id),
            record.reward_id,
            record.to_dict(),
            index=False,
        )
    except Exception:
        logger.warning("写入奖励账本失败", exc_info=True)
        return None
    return record


async def list_rewards(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    limit: int = LIST_LIMIT,
) -> list[RewardRecord]:
    if store is None or not user_id or not thread_id:
        return []
    try:
        items = await store.asearch(
            reward_namespace(user_id, thread_id),
            limit=limit,
        )
    except Exception:
        logger.warning("读取奖励账本失败", exc_info=True)
        return []
    records = [RewardRecord(**dict(item.value)) for item in items]
    records.sort(key=lambda item: item.created_at)
    return records
