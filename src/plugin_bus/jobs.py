"""插件安装/更新异步任务表。

结构与 ``api/skills.py`` 的任务字段对齐，前端复用同一套底部进度卡；
本模块只做内存任务登记，不负责安装逻辑。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

JOB_TTL_SECONDS = 30 * 60
MAX_JOBS = 40

STAGE_PROGRESS = {
    "queued": 0,
    "downloading": 15,
    "scanning": 30,
    "sandbox_verifying": 50,
    "extracting": 62,
    "installing": 75,
    "starting": 88,
    "done": 100,
    "error": 100,
}

_JOBS: dict[str, dict[str, Any]] = {}


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def _prune() -> None:
    now = time.time()
    finished = [
        (job_id, job)
        for job_id, job in _JOBS.items()
        if job["status"] in {"done", "error"}
    ]
    for job_id, job in finished:
        if now - float(job["_updated_ts"]) > JOB_TTL_SECONDS:
            _JOBS.pop(job_id, None)
    overflow = len(_JOBS) - MAX_JOBS
    if overflow > 0:
        oldest = sorted(_JOBS.items(), key=lambda item: float(item[1]["_updated_ts"]))[
            :overflow
        ]
        for job_id, job in oldest:
            if job["status"] in {"done", "error"}:
                _JOBS.pop(job_id, None)


def create_job(kind: str, label: str, *, user_id: str, plugin_id: str = "") -> dict[str, Any]:
    _prune()
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    job: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "plugin_id": plugin_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "排队中",
        "error": "",
        "result": None,
        "created_at": now,
        "updated_at": now,
        "_updated_ts": time.time(),
        "_user_id": user_id,
    }
    _JOBS[job_id] = job
    return job


def update_job(job_id: str, **fields: Any) -> None:
    job = _JOBS.get(job_id)
    if job is None:
        return
    stage = fields.get("stage")
    if stage and "progress" not in fields:
        fields["progress"] = STAGE_PROGRESS.get(str(stage), job["progress"])
    job.update(fields)
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    job["_updated_ts"] = time.time()


def fail_job(job_id: str, message: str) -> None:
    update_job(
        job_id,
        status="error",
        stage="error",
        message=message,
        error=message,
    )


def get_job(user_id: str, job_id: str) -> dict[str, Any] | None:
    job = _JOBS.get(job_id)
    if job is None or job.get("_user_id") != user_id:
        return None
    return _public(job)


def list_jobs(user_id: str) -> list[dict[str, Any]]:
    _prune()
    jobs = sorted(
        (job for job in _JOBS.values() if job.get("_user_id") == user_id),
        key=lambda item: float(item["_updated_ts"]),
        reverse=True,
    )
    return [_public(job) for job in jobs[:MAX_JOBS]]


__all__ = [
    "STAGE_PROGRESS",
    "create_job",
    "update_job",
    "fail_job",
    "get_job",
    "list_jobs",
]
