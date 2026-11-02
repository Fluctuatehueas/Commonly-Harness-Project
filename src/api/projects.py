"""项目工作区管理接口。

- GET    /projects                      项目列表（含状态、对话数）
- POST   /projects                      新建（create）或附着（attach）项目
- POST   /projects/validate             只校验目录与标记，不改状态
- POST   /projects/{id}/validate        重新校验并修复标记
- PATCH  /projects/{id}                 重命名 / 置顶
- DELETE /projects/{id}                 仅移出登记，不删除磁盘内容
- GET    /projects/{id}/conversations   项目下对话
- GET    /projects/{id}/artifacts       项目产物发布记录

登记持久化在 ``USER_DATA_DIR/projects.json``；目录信任由
``.commonharness/project.json`` 标记证明。
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent import project_store
from agent.project_marker import (
    ProjectPathError,
    marker_state,
    read_marker,
    validate_directory,
)
from api.agent_loader import agent_loader
from api.history import _session_from_index
from api.history import serialize_messages


router = APIRouter(tags=["projects"])


class ProjectCreateRequest(BaseModel):
    """POST /projects 请求体。"""

    mode: Literal["create", "attach"] = "attach"
    name: str = Field("", max_length=120)
    path: str = Field(..., min_length=1, max_length=1000)
    adopt: bool = False


class ProjectUpdateRequest(BaseModel):
    """PATCH /projects/{id} 请求体。"""

    name: str | None = Field(None, min_length=1, max_length=120)
    pinned: bool | None = None


class ProjectPathCheckRequest(BaseModel):
    """POST /projects/validate 请求体。"""

    path: str = Field(..., min_length=1, max_length=1000)


def _raise_path_error(error: ProjectPathError) -> None:
    raise HTTPException(
        status_code=422,
        detail={"code": error.code, "message": error.message},
    )


def _serialize(record: dict[str, Any], conversation_count: int = 0) -> dict[str, Any]:
    """项目记录的对外结构。"""
    return {
        "id": record["id"],
        "name": record["name"],
        "path": record["path"],
        "pinned": bool(record.get("pinned", False)),
        "marker_state": record.get("marker_state", "missing"),
        "status": record.get("status", "untrusted"),
        "conversation_count": conversation_count,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_opened_at": record.get("last_opened_at") or None,
        "is_default": record["id"] == project_store.DEFAULT_PROJECT_ID,
    }


@router.get("/projects")
async def list_projects(
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """返回项目列表，并附带每个项目的对话数量。"""
    records = await asyncio.to_thread(project_store.list_projects, user_id)
    sessions = await agent_loader.list_sessions(user_id)
    counts = Counter(
        str(session.get("project_id") or "")
        for session in sessions
        if session.get("project_id")
    )
    return {
        "projects": [
            _serialize(record, counts.get(record["id"], 0)) for record in records
        ]
    }


@router.post("/projects")
async def create_project(
    body: ProjectCreateRequest,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """新建项目目录或附着已有目录；以后端创建的标记为信任凭证。"""
    try:
        record = await asyncio.to_thread(
            project_store.create_project,
            user_id=user_id,
            mode=body.mode,
            name=body.name,
            path_text=body.path,
            adopt=body.adopt,
        )
    except ProjectPathError as error:
        _raise_path_error(error)
    return _serialize(record)


@router.post("/projects/validate")
async def validate_path(body: ProjectPathCheckRequest) -> dict[str, Any]:
    """只校验目录与标记，不写登记表。"""
    try:
        root = await asyncio.to_thread(validate_directory, body.path)
    except ProjectPathError as error:
        return {
            "ok": False,
            "code": error.code,
            "message": error.message,
            "marker_state": "unknown",
        }

    marker = read_marker(root)
    if marker is None:
        return {
            "ok": True,
            "path": str(root),
            "marker_state": "missing",
            "message": "目录可用，尚未创建 .commonharness 标记",
            "project_id": None,
        }
    state, detail = marker_state(root, str(marker.get("project_id") or ""))
    return {
        "ok": state == "ok",
        "path": str(root),
        "marker_state": state,
        "message": detail,
        "project_id": marker.get("project_id"),
    }


@router.post("/projects/{project_id}/validate")
async def validate_project(
    project_id: str,
    user_id: str = Query("anonymous", min_length=1),
    repair: bool = Query(True),
) -> dict[str, Any]:
    """重新校验项目；默认重建标记，用于目录被移动或标记被删除后的修复。"""
    try:
        record = await asyncio.to_thread(
            project_store.validate_project, user_id, project_id, repair=repair
        )
    except ProjectPathError as error:
        _raise_path_error(error)
    return _serialize(record)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdateRequest,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """重命名或置顶项目。"""
    if body.name is None and body.pinned is None:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")
    try:
        record = await asyncio.to_thread(
            project_store.update_project,
            user_id,
            project_id,
            name=body.name,
            pinned=body.pinned,
        )
    except ProjectPathError as error:
        _raise_path_error(error)
    return _serialize(record)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """仅移出登记，不删除磁盘目录。"""
    try:
        await asyncio.to_thread(project_store.delete_project, user_id, project_id)
    except ProjectPathError as error:
        _raise_path_error(error)
    return {"ok": True}


@router.get("/projects/{project_id}/conversations")
async def list_project_conversations(
    project_id: str,
    user_id: str = Query("anonymous", min_length=1),
) -> dict[str, Any]:
    """返回项目下的对话，沿用 /history 的消息计数规则。"""
    if await asyncio.to_thread(project_store.get_project, user_id, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    indexed = await agent_loader.list_sessions(user_id)
    sessions = []
    for item in indexed:
        if str(item.get("project_id") or "") != project_id:
            continue
        messages = await agent_loader.get_thread_messages(item["thread_id"])
        sessions.append(
            _session_from_index(item, message_count=len(serialize_messages(messages)))
        )
    return {"sessions": sessions}


@router.get("/projects/{project_id}/artifacts")
async def list_project_artifacts(
    project_id: str,
    user_id: str = Query("anonymous", min_length=1),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """返回项目产物发布记录（来自 Store 审计数据）。"""
    if await asyncio.to_thread(project_store.get_project, user_id, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"artifacts": await agent_loader.list_artifacts(user_id, project_id, limit)}
