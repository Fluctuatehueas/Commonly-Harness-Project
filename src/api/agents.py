"""MyAgent 管理接口：查看、招募、编辑、删除用户自己的主 Agent。

所有档案都存放在 PostgreSQL Store 的 ``("agent_profiles", user_id)``，
与用户身份强绑定；切换用户后互不可见。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.agent_profiles import (
    DEFAULT_AGENT_ID,
    AgentProfileStore,
    public_profile,
)
from agent.agent_team.store import AgentGroupStore
from api.agent_loader import agent_loader
from api.identity import effective_user_id


router = APIRouter(prefix="/agents", tags=["myagent"])


class AgentModelPayload(BaseModel):
    model_name: str | None = Field(None, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=300)
    thinking_enabled: bool | None = None
    context_limit: int | None = Field(None, ge=16000, le=2_000_000)


class AgentPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    role_title: str = Field("", max_length=80)
    description: str = Field("", max_length=500)
    avatar: str = Field("", max_length=2)
    system_prompt: str = Field("", max_length=20_000)
    model: AgentModelPayload | None = None
    mcp_server_ids: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class AgentUpdatePayload(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    role_title: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=500)
    avatar: str | None = Field(None, max_length=2)
    system_prompt: str | None = Field(None, max_length=20_000)
    model: AgentModelPayload | None = None
    mcp_server_ids: list[str] | None = None
    group_ids: list[str] | None = None
    enabled: bool | None = None


async def _store(user_id: str) -> AgentProfileStore:
    await agent_loader.initialize()
    store = agent_loader.store
    if store is None:
        raise HTTPException(status_code=503, detail="Agent Store 尚未初始化")
    return AgentProfileStore(store, user_id)


def _model_dict(payload: AgentModelPayload | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return payload.model_dump(exclude_none=True)


async def _sync_groups(user_id: str, agent_id: str, group_ids: list[str]) -> None:
    """把 Agent 的组籍同步到目标集合：先加入、再退出，避免中间态丢成员。"""
    store = AgentGroupStore(agent_loader.store, user_id)
    target: list[str] = []
    for group_id in group_ids:
        value = str(group_id or "").strip()
        if value and value not in target:
            target.append(value)
    current = {group["group_id"]: group for group in await store.groups_of(agent_id)}
    for group_id in target:
        if group_id not in current:
            await store.add_member(group_id, agent_id)
    for group_id in list(current):
        if group_id not in target:
            await store.remove_member(group_id, agent_id)


@router.get("")
async def list_agents(user_id: str = Query("", max_length=64)) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    profiles = await (await _store(uid)).list()
    return {
        "user_id": uid,
        "items": [public_profile(item) for item in profiles],
        "default_agent_id": "default",
    }


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    profile = await (await _store(uid)).get(agent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return public_profile(profile)


@router.post("", status_code=201)
async def create_agent(
    body: AgentPayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    store = await _store(uid)
    payload = body.model_dump(exclude_none=True)
    payload.pop("group_ids", None)
    model = _model_dict(body.model)
    if model is not None:
        payload["model"] = model
    try:
        profile = await store.create(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        if body.group_ids:
            await _sync_groups(uid, profile["id"], body.group_ids)
    except Exception:
        # 招募失败时回滚：先退出已加入的组，再删除档案，避免半成品。
        for group in await AgentGroupStore(agent_loader.store, uid).groups_of(profile["id"]):
            try:
                await AgentGroupStore(agent_loader.store, uid).remove_member(
                    group["group_id"], profile["id"]
                )
            except ValueError:
                pass
        await store.delete(profile["id"])
        raise
    await agent_loader.reset_user_agents(uid)
    return public_profile(profile)


@router.get("/{agent_id}/groups")
async def list_agent_groups(
    agent_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """反查某 Agent 的全部组与角色，供 MyAgent 页面回显。"""
    uid = effective_user_id(user_id)
    await agent_loader.initialize()
    groups = await AgentGroupStore(agent_loader.store, uid).groups_of(agent_id)
    return {
        "agent_id": agent_id,
        "items": [
            {
                "group_id": group["group_id"],
                "name": group["name"],
                "role": (
                    "leader"
                    if group.get("leader_agent_id") == agent_id
                    else "member"
                ),
            }
            for group in groups
        ],
    }


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdatePayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    store = await _store(uid)
    patch = body.model_dump(exclude_none=True)
    group_ids = patch.pop("group_ids", None)
    if body.model is not None:
        patch["model"] = _model_dict(body.model) or {}
    try:
        profile = await store.update(agent_id, patch)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if group_ids is not None:
        try:
            await _sync_groups(uid, agent_id, group_ids)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    await agent_loader.reset_user_agents(uid)
    return public_profile(profile)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, bool]:
    uid = effective_user_id(user_id)
    store = await _store(uid)
    if agent_id == DEFAULT_AGENT_ID:
        raise HTTPException(status_code=422, detail="默认 Agent 不可删除")
    group_store = AgentGroupStore(agent_loader.store, uid)
    leader_groups = await group_store.leader_groups_of(agent_id)
    if leader_groups:
        names = [group["name"] for group in leader_groups]
        raise HTTPException(
            status_code=422,
            detail=f"该 Agent 是 {len(names)} 个组的组长，请先转移组长：{names}",
        )
    for group in await group_store.groups_of(agent_id):
        try:
            await group_store.remove_member(group["group_id"], agent_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        removed = await store.delete(agent_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    await agent_loader.reset_user_agents(uid)
    return {"ok": True}
