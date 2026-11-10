"""MyAgent 管理接口：查看、招募、编辑、删除用户自己的主 Agent。

所有档案都存放在 PostgreSQL Store 的 ``("agent_profiles", user_id)``，
与用户身份强绑定；切换用户后互不可见。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.agent_profiles import (
    AgentProfileStore,
    public_profile,
)
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
    enabled: bool = True


class AgentUpdatePayload(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    role_title: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=500)
    avatar: str | None = Field(None, max_length=2)
    system_prompt: str | None = Field(None, max_length=20_000)
    model: AgentModelPayload | None = None
    mcp_server_ids: list[str] | None = None
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
    model = _model_dict(body.model)
    if model is not None:
        payload["model"] = model
    try:
        profile = await store.create(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await agent_loader.reset_user_agents(uid)
    return public_profile(profile)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdatePayload,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    uid = effective_user_id(user_id)
    store = await _store(uid)
    patch = body.model_dump(exclude_none=True)
    if body.model is not None:
        patch["model"] = _model_dict(body.model) or {}
    try:
        profile = await store.update(agent_id, patch)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if profile is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    await agent_loader.reset_user_agents(uid)
    return public_profile(profile)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    user_id: str = Query("", max_length=64),
) -> dict[str, bool]:
    uid = effective_user_id(user_id)
    store = await _store(uid)
    try:
        removed = await store.delete(agent_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    await agent_loader.reset_user_agents(uid)
    return {"ok": True}
