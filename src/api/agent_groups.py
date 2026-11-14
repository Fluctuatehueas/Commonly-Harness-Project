"""AgentTeam 组管理接口。

组记录存放在 PostgreSQL Store 的 ``("agent_groups", user_id)``，
反向索引在 ``("agent_memberships", user_id)``；两者都按用户隔离。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.agent_profiles import AgentProfileStore
from agent.agent_team.store import AgentGroupStore
from api.agent_loader import agent_loader
from api.identity import effective_user_id

router = APIRouter(prefix="/agent-groups", tags=["myagent"])

_USER_LOCKS: dict[str, asyncio.Lock] = {}


class GroupModelPayload(BaseModel):
    """与 AgentModelPayload 同构；组模型是可选的一层继承。"""

    model_name: str | None = Field(None, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=300)
    thinking_enabled: bool | None = None
    context_limit: int | None = Field(None, ge=16000, le=2_000_000)


class MemberPayload(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    duty: str = Field("", max_length=200)


class GroupPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    summary: str = Field("", max_length=500)
    leader_agent_id: str = Field("", max_length=64)
    visibility: Literal["private", "discoverable"] = "private"
    members: list[MemberPayload] = Field(default_factory=list)
    extra_subagent_names: list[str] = Field(default_factory=list)
    model: GroupModelPayload | None = None


class GroupUpdatePayload(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    summary: str | None = Field(None, max_length=500)
    visibility: Literal["private", "discoverable"] | None = None
    extra_subagent_names: list[str] | None = None
    model: GroupModelPayload | None = None


class LeaderPayload(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)


def public_group(group: dict[str, Any]) -> dict[str, Any]:
    """对外隐藏组模型密钥；与 public_profile 口径一致。"""
    data = dict(group)
    model = dict(data.get("model") or {})
    model["has_api_key"] = bool(model.pop("api_key", ""))
    data["model"] = model
    return data


async def _store(user_id: str) -> AgentGroupStore:
    await agent_loader.initialize()
    store = agent_loader.store
    if store is None:
        raise HTTPException(status_code=503, detail="Agent Store 尚未初始化")
    return AgentGroupStore(store, user_id)


def _lock(user_id: str) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(user_id, asyncio.Lock())


async def _require_agents(user_id: str, agent_ids: list[str]) -> None:
    """写组前确认 Agent 存在且启用，避免留下悬空组籍。"""
    await agent_loader.initialize()
    profiles = {
        item["id"]: item
        for item in await AgentProfileStore(agent_loader.store, user_id).list()
    }
    missing = [agent_id for agent_id in agent_ids if agent_id not in profiles]
    disabled = [
        agent_id
        for agent_id in agent_ids
        if agent_id in profiles and not bool(profiles[agent_id].get("enabled", True))
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"Agent 不存在: {missing}")
    if disabled:
        raise HTTPException(status_code=422, detail=f"Agent 已停用: {disabled}")


async def _reset(user_id: str) -> None:
    await agent_loader.reset_user_agents(user_id)


@router.get("")
async def list_groups(user_id: str = Query("", max_length=64)) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    groups = await (await _store(uid)).list()
    return {"user_id": uid, "items": [public_group(item) for item in groups]}


@router.get("/{group_id}")
async def get_group(
    group_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    group = await (await _store(uid)).get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="组不存在")
    return public_group(group)


@router.post("", status_code=201)
async def create_group(
    body: GroupPayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    member_ids = [member.agent_id for member in body.members]
    leader_id = body.leader_agent_id.strip()
    await _require_agents(uid, [*([leader_id] if leader_id else []), *member_ids])
    payload = body.model_dump(exclude_none=True)
    if body.model is not None:
        payload["model"] = body.model.model_dump(exclude_none=True)
    async with _lock(uid):
        try:
            group = await (await _store(uid)).create(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)


@router.put("/{group_id}")
async def update_group(
    group_id: str,
    body: GroupUpdatePayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    patch = body.model_dump(exclude_none=True)
    if body.model is not None:
        # 空对象表示清空组模型；exclude_none 会丢掉显式空 dict，这里补回。
        patch["model"] = body.model.model_dump(exclude_none=True)
    async with _lock(uid):
        try:
            group = await (await _store(uid)).update_group(group_id, patch)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, bool]:
    uid = effective_user_id(user_id)
    async with _lock(uid):
        removed = await (await _store(uid)).delete(group_id)
    if not removed:
        raise HTTPException(status_code=404, detail="组不存在")
    await _reset(uid)
    return {"ok": True}


@router.post("/{group_id}/members", status_code=201)
async def add_member(
    group_id: str,
    body: MemberPayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    await _require_agents(uid, [body.agent_id])
    async with _lock(uid):
        try:
            group = await (await _store(uid)).add_member(
                group_id, body.agent_id, body.duty
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)


@router.delete("/{group_id}/members/{agent_id}")
async def remove_member(
    group_id: str,
    agent_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    async with _lock(uid):
        try:
            group = await (await _store(uid)).remove_member(group_id, agent_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)


@router.put("/{group_id}/leader")
async def transfer_leader(
    group_id: str,
    body: LeaderPayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    await _require_agents(uid, [body.agent_id])
    async with _lock(uid):
        try:
            group = await (await _store(uid)).transfer_leader(group_id, body.agent_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)


@router.delete("/{group_id}/leader")
async def clear_leader(
    group_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """取消队长；组暂时无队长时不可作为团队会话入口。"""
    uid = effective_user_id(user_id)
    async with _lock(uid):
        try:
            group = await (await _store(uid)).clear_leader(group_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await _reset(uid)
    return public_group(group)
