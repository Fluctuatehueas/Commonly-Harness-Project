"""工具健康度聚合。

按用户持久化每个工具的调用次数、错误分类、延迟与奖励 EMA。低频工具
使用 Wilson 置信下限，避免小样本误判；``critical`` 工具只告警，不自动
降权或隐藏。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from agent.config import (
    EVOLUTION_TOOL_HEALTH_ENABLED,
    EVOLUTION_TOOL_HEALTH_MIN_SAMPLES,
)
from agent.evolution.types import ToolHealthRecord

logger = logging.getLogger(__name__)

TOOL_HEALTH_NAMESPACE_PREFIX = ("tool_health",)
MAX_LATENCIES = 200
EMA_ALPHA = 0.2


def tool_health_namespace(user_id: str) -> tuple[str, str]:
    return (*TOOL_HEALTH_NAMESPACE_PREFIX, user_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)


def _server_and_tool(tool_name: str) -> tuple[str, str]:
    if "__" in tool_name:
        server_id, remote = tool_name.split("__", 1)
        return server_id or "mcp", remote
    return "builtin", tool_name


def _criticality(server_id: str) -> str:
    try:
        from agent.mcp.runtime import mcp_runtime

        config = mcp_runtime.config_for(server_id)
        if config is not None:
            return str(config.criticality or "optional")
    except Exception:
        pass
    return "optional"


def wilson_lower_bound(successes: int, samples: int, z: float = 1.96) -> float:
    """Wilson score 置信区间下限。"""
    if samples <= 0:
        return 0.0
    phat = successes / samples
    denominator = 1 + z * z / samples
    centre = phat + z * z / (2 * samples)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * samples)) / samples)
    return max(0.0, (centre - margin) / denominator)


async def record_tool_health(
    store: Any,
    *,
    user_id: str,
    tool_name: str,
    category: str,
    reward_value: float,
    duration_ms: int | None = None,
) -> ToolHealthRecord | None:
    if not EVOLUTION_TOOL_HEALTH_ENABLED or store is None or not user_id or not tool_name:
        return None
    server_id, remote_name = _server_and_tool(tool_name)
    namespace = tool_health_namespace(user_id)
    key = f"{_safe_id(server_id)}:{_safe_id(remote_name)}"
    try:
        item = await store.aget(namespace, key)
        value = dict(item.value) if item is not None else {}
        record = ToolHealthRecord(
            server_id=server_id,
            tool_name=tool_name,
            criticality=str(value.get("criticality") or _criticality(server_id)),
            samples=int(value.get("samples") or 0),
            successes=int(value.get("successes") or 0),
            tool_errors=int(value.get("tool_errors") or 0),
            infra_errors=int(value.get("infra_errors") or 0),
            latencies=list(value.get("latencies") or []),
            reward_ema=float(value.get("reward_ema") or 0.0),
            last_error_category=str(value.get("last_error_category") or ""),
            updated_at=str(value.get("updated_at") or ""),
        )
        record.samples += 1
        if category == "success":
            record.successes += 1
        elif category == "infra_error":
            record.infra_errors += 1
            record.last_error_category = "infra_error"
        else:
            record.tool_errors += 1
            record.last_error_category = category
        if duration_ms is not None and duration_ms >= 0:
            record.latencies.append(int(duration_ms))
            record.latencies = record.latencies[-MAX_LATENCIES:]
        record.reward_ema = round(
            (1 - EMA_ALPHA) * record.reward_ema + EMA_ALPHA * float(reward_value),
            4,
        )
        record.updated_at = _now()
        await store.aput(namespace, key, record.to_dict(), index=False)
        return record
    except Exception:
        logger.warning("更新工具健康度失败：%s", tool_name, exc_info=True)
        return None


def health_view(record: ToolHealthRecord) -> dict[str, Any]:
    data = record.to_dict()
    data["success_rate"] = round(record.success_rate, 4)
    data["confidence_lcb"] = round(
        wilson_lower_bound(record.successes, record.samples), 4
    )
    data["latency_p50_ms"] = record.latency_p50
    data["latency_p95_ms"] = record.latency_p95
    data["exploration_budget"] = max(
        0, EVOLUTION_TOOL_HEALTH_MIN_SAMPLES - record.samples
    )
    data["low_sample"] = record.samples < EVOLUTION_TOOL_HEALTH_MIN_SAMPLES
    data["critical_warning_only"] = record.criticality == "critical"
    return data


async def list_tool_health(
    store: Any,
    *,
    user_id: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    if store is None or not user_id:
        return []
    try:
        items = await store.asearch(tool_health_namespace(user_id), limit=limit)
    except Exception:
        logger.warning("读取工具健康度失败", exc_info=True)
        return []
    records = [ToolHealthRecord(**dict(item.value)) for item in items]
    records.sort(key=lambda item: (item.tool_errors + item.infra_errors, item.samples), reverse=True)
    return [health_view(record) for record in records]
