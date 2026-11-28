"""轨迹查询 gRPC 服务：按 run 聚合 TrajectoryMiddleware 写入的事件。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402

import Agent_pb2  # noqa: E402
import Trajectory_pb2  # noqa: E402
import Trajectory_pb2_grpc  # noqa: E402

from api.agent_loader import AgentLoader  # noqa: E402
from api.trajectory import _build_run  # noqa: E402


KNOWN_EVENT_KEYS = {
    "event_id",
    "id",
    "run_id",
    "thread_id",
    "parent_id",
    "type",
    "name",
    "status",
    "start",
    "started_at",
    "end",
    "ended_at",
    "args",
    "result",
}


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _metadata_json(event: dict[str, Any]) -> str:
    extra = {
        key: value
        for key, value in event.items()
        if key not in KNOWN_EVENT_KEYS and value is not None
    }
    return json.dumps(extra, ensure_ascii=False, default=str)


def _to_proto_event(event: dict[str, Any], run_id: str, index: int) -> Agent_pb2.TrajectoryEvent:
    return Agent_pb2.TrajectoryEvent(
        event_id=str(
            event.get("event_id")
            or event.get("id")
            or f"{run_id}-{index}"
        ),
        run_id=str(event.get("run_id") or run_id),
        thread_id=str(event.get("thread_id") or ""),
        parent_id=str(event.get("parent_id") or ""),
        type=str(event.get("type") or ""),
        name=str(event.get("name") or ""),
        status=str(event.get("status") or ""),
        args_json=_json_text(event.get("args")),
        result_json=_json_text(event.get("result")),
        started_at=str(event.get("start") or event.get("started_at") or ""),
        ended_at=str(event.get("end") or event.get("ended_at") or ""),
        metadata_json=_metadata_json(event),
    )


class TrajectoryServiceServicer(Trajectory_pb2_grpc.TrajectoryServiceServicer):
    """只读轨迹服务；数据来源与 HTTP /trajectory 完全一致。"""

    def __init__(self, loader: AgentLoader) -> None:
        self._loader = loader

    async def GetTrajectory(
        self,
        request: Trajectory_pb2.GetTrajectoryRequest,
        context: grpc.aio.ServicerContext,
    ) -> Trajectory_pb2.GetTrajectoryResponse:
        user_id = request.user_id.strip() or "anonymous"
        if await self._loader.get_session(user_id, request.thread_id) is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        events = await self._loader.list_trajectory(user_id, request.thread_id)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            run_id = str(event.get("run_id", "") or "unknown")
            grouped.setdefault(run_id, []).append(event)

        runs = [
            _build_run(run_id, run_events)
            for run_id, run_events in grouped.items()
        ]
        runs.sort(key=lambda item: item["started_at"])

        response = Trajectory_pb2.GetTrajectoryResponse(
            thread_id=request.thread_id,
            latest_run_id=runs[-1]["run_id"] if runs else "",
            event_count=len(events),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        for run in runs:
            proto_run = Trajectory_pb2.TrajectoryRun(
                run_id=str(run["run_id"]),
                started_at=str(run.get("started_at") or ""),
                ended_at=str(run.get("ended_at") or ""),
                status=str(run.get("status") or ""),
                summary_json=json.dumps(
                    run.get("summary") or {},
                    ensure_ascii=False,
                    default=str,
                ),
            )
            for index, event in enumerate(run.get("events") or []):
                proto_run.events.append(
                    _to_proto_event(event, str(run["run_id"]), index)
                )
            response.runs.append(proto_run)

        return response
