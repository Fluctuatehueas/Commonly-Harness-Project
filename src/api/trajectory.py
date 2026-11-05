"""轨迹（Trajectory）查询接口。

数据由 ``TrajectoryMiddleware`` 在每轮 Agent 执行过程中写入 LangGraph Store，
本接口只负责按 run 聚合、排序和汇总，不参与 Agent 执行。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.agent_loader import agent_loader
from agent.config import COMPACTION_SIGNING_KEY


router = APIRouter(prefix="/trajectory", tags=["trajectory"])

CATEGORY_KEYS = ("model", "tool", "skill", "delegation", "hitl", "compaction")
# 压缩后允许找回原文的观察窗口：最多统计 5 次工具读取。
RETRIEVAL_WINDOW_READS = 5


def _estimate_tokens(text: Any) -> int:
    value = str(text or "")
    if not value:
        return 0
    # 中英混排粗估：CJK 约 1 字 1 token，ASCII 约 4 字符 1 token。
    cjk = sum(1 for char in value if "\u4e00" <= char <= "\u9fa5")
    return cjk + max((len(value) - cjk) // 4, 0)


def _attach_retrieval_cost(events: list[dict[str, Any]]) -> None:
    """压缩后回溯成本：统计后续工具事件里读取归档文件次数与结果 token。"""
    for index, event in enumerate(events):
        if str(event.get("type")) != "compaction":
            continue
        result = event.get("compaction_result") or {}
        history_file = str(result.get("history_file") or "")
        if not history_file:
            continue
        reads = 0
        tokens = 0
        for later in events[index + 1 :]:
            if str(later.get("type")) != "tool":
                continue
            args = str(later.get("args") or "")
            if history_file not in args:
                continue
            reads += 1
            tokens += _estimate_tokens(later.get("result"))
            if reads >= RETRIEVAL_WINDOW_READS:
                break
        evaluation = dict(event.get("evaluation") or {})
        evaluation["retrieval_cost"] = {"reads": reads, "tokens": tokens}
        event["evaluation"] = evaluation


def _attach_integrity_check(events: list[dict[str, Any]]) -> None:
    """重算压缩回执摘要，标记完整性是否通过（可选校验 HMAC 签名）。"""
    for event in events:
        if str(event.get("type")) != "compaction":
            continue
        integrity = dict(event.get("integrity") or {})
        if not integrity.get("hash"):
            continue
        core = {
            key: event.get(key)
            for key in (
                "compaction_id",
                "trigger",
                "scope",
                "compaction_result",
                "start",
                "end",
            )
        }
        digest = hashlib.sha256(
            json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        verified = hmac.compare_digest(digest, str(integrity.get("hash") or ""))
        if verified and integrity.get("signed") and COMPACTION_SIGNING_KEY:
            signature = hmac.new(
                COMPACTION_SIGNING_KEY.encode("utf-8"),
                digest.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            verified = hmac.compare_digest(
                signature, str(integrity.get("signature") or "")
            )
        integrity["verified"] = verified
        event["integrity"] = integrity


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _duration_ms(start: str, end: str | None) -> int:
    started = _parse_time(start)
    ended = _parse_time(end)
    if started is None or ended is None:
        return 0
    return max(int((ended - started).total_seconds() * 1000), 0)


def _run_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """按事件类别统计次数、Token 与上下文峰值。"""
    counts = {key: 0 for key in CATEGORY_KEYS}
    tokens = {"input": 0, "output": 0, "total": 0}
    peak_context = 0
    min_context = 0
    tokens_saved = 0
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type in counts:
            counts[event_type] += 1
        usage = event.get("tokens") or {}
        if isinstance(usage, dict):
            for key in tokens:
                tokens[key] += int(usage.get(key) or 0)
        context = event.get("context") or {}
        if isinstance(context, dict) and context.get("used_tokens"):
            used = int(context.get("used_tokens") or 0)
            peak_context = max(peak_context, used)
            min_context = used if min_context == 0 else min(min_context, used)
        if event_type == "compaction":
            result = event.get("compaction_result") or {}
            if isinstance(result, dict):
                tokens_saved += max(int(result.get("saved_tokens") or 0), 0)
    return {
        "model_calls": counts["model"],
        "tool_calls": counts["tool"],
        "skill_calls": counts["skill"],
        "delegations": counts["delegation"],
        "hitl": counts["hitl"],
        "compact_count": counts["compaction"],
        "tokens": tokens,
        "peak_context_tokens": peak_context,
        "min_context_tokens": min_context,
        "tokens_saved": tokens_saved,
    }


def _sort_time(event: dict[str, Any]) -> str:
    """run_end 的 start 与 run_start 相同，按 end 排序才能落在列表末尾。"""
    if event.get("type") == "run" and event.get("status") == "done":
        return str(event.get("end") or event.get("start") or "")
    return str(event.get("start") or "")


def _build_run(run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """把同一 run 的事件聚合成前端可直接渲染的结构。"""
    ordered = sorted(events, key=_sort_time)
    start_event = next(
        (event for event in ordered if event.get("type") == "run"),
        ordered[0],
    )
    end_event = next(
        (
            event
            for event in reversed(ordered)
            if event.get("type") == "run" and event.get("status") == "done"
        ),
        None,
    )
    started_at = str(start_event.get("start", ""))
    ended_at = str(end_event.get("end", "")) if end_event else None
    hitl_pending = any(
        event.get("type") == "hitl"
        and str(event.get("status", "")) in {"interrupted", "running"}
        for event in ordered
    )
    status = "done" if ended_at else ("interrupted" if hitl_pending else "running")
    summary = _run_summary(ordered)
    summary["duration_ms"] = _duration_ms(started_at, ended_at)
    summary["event_count"] = len(ordered)
    return {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
        "events": ordered,
        "summary": summary,
    }


@router.get("/{thread_id}")
async def get_trajectory(
    thread_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """返回指定会话的全部轨迹 run（按时间升序）。"""
    if await agent_loader.get_session(user_id, thread_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    events = await agent_loader.list_trajectory(user_id, thread_id)
    _attach_retrieval_cost(events)
    _attach_integrity_check(events)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        run_id = str(event.get("run_id", "") or "unknown")
        grouped.setdefault(run_id, []).append(event)

    runs = [
        _build_run(run_id, run_events)
        for run_id, run_events in grouped.items()
    ]
    runs.sort(key=lambda item: item["started_at"])
    return {
        "thread_id": thread_id,
        "runs": runs,
        "latest_run_id": runs[-1]["run_id"] if runs else None,
        "event_count": len(events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
