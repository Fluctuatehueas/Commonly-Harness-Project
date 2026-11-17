"""调度运行的事件流。

后台调度工具返回后，本轮聊天的 SSE 已经关闭，进度改由本接口按 ``run_id``
推送。事件来源是进程内 ``scheduler_events`` 总线；订阅时先补一份数据库快照，
再回放历史事件并转入实时推送，因此刷新或断线重连都能恢复卡片状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.agent_loader import agent_loader
from scheduler.events import scheduler_events
from scheduler.models import SchedulePlan
from scheduler.runner import plan_items_payload
from scheduler.store import SchedulerStore

logger = logging.getLogger(__name__)

router = APIRouter()

TERMINAL_STATUSES = {"done", "failed", "paused", "interrupted", "cancelled"}
HEARTBEAT_SECONDS = 15.0
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _load_run(
    run_id: str,
) -> tuple[dict[str, Any] | None, SchedulePlan | None]:
    store = SchedulerStore()
    try:
        row = await store.get_run(run_id)
        plan_row = await store.latest_plan(run_id)
    finally:
        await store.close()
    plan: SchedulePlan | None = None
    if plan_row:
        try:
            plan = SchedulePlan.model_validate(plan_row)
        except Exception:
            logger.warning("调度计划反序列化失败：%s", run_id, exc_info=True)
    return row, plan


def _planned_event(
    run_id: str, row: dict[str, Any] | None, plan: SchedulePlan | None
) -> dict[str, Any] | None:
    """用数据库快照重建卡片；事件名沿用 planned，前端可直接复用渲染逻辑。"""
    if plan is None:
        return None
    items = [item for phase in plan.phases for item in phase.items]
    return {
        "type": "scheduler_progress",
        "event": "planned",
        "run_id": run_id,
        "goal": plan.goal,
        "tier": plan.tier.value,
        "mode": plan.mode,
        "plan_version": plan.plan_version,
        "item_count": len(items),
        "waves": len(plan.waves),
        "plan_items": plan_items_payload(plan),
        "status": str((row or {}).get("status") or "running"),
        # 该接口只服务后台执行，前端据此重建卡片并继续订阅。
        "background": True,
    }


def _finished_event(run_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    state = (row or {}).get("state") or {}
    status = str((row or {}).get("status") or state.get("status") or "done")
    return {
        "type": "scheduler_progress",
        "event": "run_finished",
        "run_id": run_id,
        "status": status,
        "summary": str(state.get("summary") or ""),
        "plan_version": int((row or {}).get("plan_version") or 1),
        "done": int(state.get("done_count") or 0),
        "failed": int(state.get("failed_count") or 0),
        "replans": int(state.get("replan_count") or 0),
        "warnings": list(state.get("warnings") or []),
    }


def _run_summary(row: dict[str, Any]) -> dict[str, Any]:
    """把 scheduler_run 行裁剪成任务栏需要的字段。"""
    state = row.get("state") or {}
    return {
        "run_id": str(row.get("run_id") or ""),
        "goal": str(row.get("goal") or ""),
        "status": str(row.get("status") or "unknown"),
        "tier": str(row.get("tier") or ""),
        "mode": str(row.get("mode") or ""),
        "plan_version": int(row.get("plan_version") or 1),
        "item_count": int(state.get("item_count") or 0),
        "done": int(state.get("done_count") or 0),
        "failed": int(state.get("failed_count") or 0),
        "summary": str(state.get("summary") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/scheduler/runs")
async def list_scheduler_runs(
    user_id: str = Query(..., min_length=1),
    thread_id: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """列出当前会话的调度运行，供输入框上方的后台任务栏展示。"""
    await agent_loader.require_session(user_id, thread_id)
    store = SchedulerStore()
    try:
        rows = await store.list_runs(user_id, thread_id, limit)
    except Exception:
        # 后台任务栏是辅助信息，数据库短暂不可用时不阻塞聊天输入。
        logger.warning("读取调度运行列表失败", exc_info=True)
        rows = []
    finally:
        await store.close()
    return {"runs": [_run_summary(row) for row in rows]}


@router.post("/scheduler/runs/{run_id}/cancel")
async def cancel_scheduler_run_endpoint(
    run_id: str,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """终止进程内正在执行的后台调度；终态由任务自身落库并通过事件流广播。"""
    row, _ = await _load_run(run_id)
    if row is None or str(row.get("user_id") or "") != user_id:
        raise HTTPException(status_code=404, detail="调度运行不存在或无权访问")
    await agent_loader.require_session(user_id, str(row.get("thread_id") or ""))

    from agent.tools.scheduler_tools import cancel_scheduler_run

    if cancel_scheduler_run(run_id):
        return {"ok": True, "run_id": run_id, "status": "cancelling"}

    # paused/created 的运行没有进程内任务：允许直接把数据库状态落成取消。
    status = str(row.get("status") or "")
    if status in {"created", "paused"}:
        store = SchedulerStore()
        try:
            await store.mark_cancelled(run_id)
        finally:
            await store.close()
        scheduler_events.publish(
            run_id,
            {
                "event": "run_finished",
                "run_id": run_id,
                "status": "cancelled",
                "summary": "后台调度已由用户终止",
                "warnings": [],
            },
        )
        return {"ok": True, "run_id": run_id, "status": "cancelled"}

    raise HTTPException(status_code=409, detail="任务已结束或不在当前进程中运行")


@router.get("/scheduler/runs/{run_id}/events")
async def scheduler_run_events(
    run_id: str,
    user_id: str = Query(..., min_length=1),
    after_seq: int = Query(0, ge=0),
) -> StreamingResponse:
    """订阅一次调度运行的进度事件，直到运行进入终态。"""
    try:
        row, plan = await _load_run(run_id)
    except Exception:
        # 数据库不可用时仍允许消费进程内事件，避免后台进度完全不可见。
        logger.warning("读取调度运行失败，降级为内存事件：%s", run_id, exc_info=True)
        row, plan = None, None

    if row is not None:
        if str(row.get("user_id") or "") != user_id:
            raise HTTPException(status_code=404, detail="调度运行不存在或无权访问")
        await agent_loader.require_session(user_id, str(row.get("thread_id") or ""))
        status = str(row.get("status") or "")
    elif scheduler_events.has(run_id):
        status = ""
    else:
        raise HTTPException(status_code=404, detail="调度运行不存在或已过期")

    async def stream() -> AsyncIterator[str]:
        cursor = after_seq
        header = _planned_event(run_id, row, plan)
        if header is None:
            header = scheduler_events.snapshot(run_id, "planned")
            if header is not None:
                cursor = max(cursor, int(header.get("seq") or 0))
        if header is not None:
            yield _sse(header)

        if status in TERMINAL_STATUSES:
            yield _sse(_finished_event(run_id, row))
            return

        history, queue, close = scheduler_events.open(run_id, cursor)
        last_seq = cursor
        try:
            for event in history:
                seq = int(event.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield _sse(event)
                if event.get("event") == "run_finished":
                    return

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # SSE 注释帧：保持连接存活，不进入前端消息处理。
                    yield ": ping\n\n"
                    continue
                seq = int(event.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield _sse(event)
                if event.get("event") == "run_finished":
                    return
        finally:
            close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


__all__ = ["router"]
