"""把 Store 记录装配成上下文对象。所有不变量在这里被触发。"""

from __future__ import annotations

from typing import Any

from agent.agent_profiles import DEFAULT_AGENT_ID, AgentProfileStore
from agent.agent_team.context import (
    AgentCapabilities,
    AgentCard,
    AgentIdentity,
    GroupContext,
    GroupDescriptor,
    GroupMemberContext,
    GroupMembership,
    GroupRole,
    GroupVisibility,
    HarnessAgentContextBase,
    ModelOverride,
    StandaloneAgentContext,
    TeamAgentContext,
)
from agent.agent_team.store import AgentGroupStore
from agent.memory.prompts import system_prompt as CODE_DEFAULT_AGENT_PROMPT


class GroupNotFoundError(LookupError):
    """生效组不存在。"""


class NotGroupMemberError(PermissionError):
    """Agent 不在请求的组里。"""


def _identity(profile: dict[str, Any]) -> AgentIdentity:
    return AgentIdentity(
        agent_id=str(profile["id"]),
        name=str(profile.get("name") or profile["id"]),
        role_title=str(profile.get("role_title") or ""),
        description=str(profile.get("description") or ""),
        avatar=str(profile.get("avatar") or "A"),
    )


def _descriptor(group: dict[str, Any]) -> GroupDescriptor:
    return GroupDescriptor(
        group_id=str(group["group_id"]),
        name=str(group["name"]),
        summary=str(group.get("summary") or ""),
        leader_agent_id=str(group.get("leader_agent_id") or ""),
        visibility=GroupVisibility(str(group.get("visibility") or "private")),
        member_count=len(group.get("members") or []),
    )


def _memberships(agent_id: str, groups: list[dict[str, Any]]) -> tuple[GroupMembership, ...]:
    result: list[GroupMembership] = []
    for group in groups:
        for member in group.get("members") or []:
            if member.get("agent_id") != agent_id:
                continue
            result.append(
                GroupMembership(
                    group_id=str(group["group_id"]),
                    role=GroupRole(str(member.get("role") or "member")),
                    duty=str(member.get("duty") or ""),
                    joined_at=str(member.get("joined_at") or ""),
                )
            )
    return tuple(result)


class AgentContextFactory:
    """记录 → 上下文对象的唯一入口。"""

    def __init__(self, store: Any, user_id: str) -> None:
        self._profiles = AgentProfileStore(store, user_id)
        self._groups = AgentGroupStore(store, user_id)
        self._user_id = user_id

    # ---------- 旧路径：不读组 ----------

    @staticmethod
    def from_profile(profile: dict[str, Any] | None) -> HarnessAgentContextBase:
        """没有任何组归属的普通 Agent；主链路无组时走这里。"""
        data = dict(profile or {})
        identity = (
            _identity(data)
            if data.get("id")
            else AgentIdentity(
                agent_id=DEFAULT_AGENT_ID,
                name="默认主 Agent",
                role_title="通用任务执行者",
            )
        )
        return StandaloneAgentContext(
            identity=identity,
            system_prompt=str(data.get("system_prompt") or "").strip()
            or CODE_DEFAULT_AGENT_PROMPT.strip(),
            model=ModelOverride.from_profile(data.get("model")),
            capabilities=AgentCapabilities.from_profile(data),
        )

    # ---------- 新路径：带生效组 ----------

    async def build(
        self, agent_id: str, active_group_id: str | None
    ) -> HarnessAgentContextBase:
        profile = await self._resolve_profile(agent_id)
        if not active_group_id:
            return self.from_profile(profile)

        group = await self._groups.get(active_group_id)
        if group is None:
            raise GroupNotFoundError(active_group_id)
        return await self.build_within(profile, group)

    async def build_within(
        self,
        profile: dict[str, Any],
        group: dict[str, Any],
        *,
        memberships: tuple[GroupMembership, ...] | None = None,
        discoverable: list[dict[str, Any]] | None = None,
        profile_map: dict[str, dict[str, Any]] | None = None,
    ) -> TeamAgentContext:
        """在给定组内构建上下文。

        装配成员子 Agent 时可传入预读的 ``memberships`` / ``profile_map``，
        避免每个成员都全量扫描一次组与档案。
        """
        agent_id = str(profile["id"])
        if memberships is None:
            memberships = _memberships(agent_id, await self._groups.groups_of(agent_id))
        role = next(
            (item.role for item in memberships if item.group_id == group["group_id"]),
            None,
        )
        if role is None:
            raise NotGroupMemberError(f"{agent_id} 不在组 {group['group_id']} 中")

        context = GroupContext(
            active_group=_descriptor(group),
            members=await self._enrich_cards(group, profile_map=profile_map),
            discoverable_groups=(
                tuple(_descriptor(item) for item in discoverable or [])
                if role is GroupRole.LEADER
                else ()
            ),
            # 组级模型策略：空对象表示不提供，成员直接继承用户默认
            model=ModelOverride.from_profile(group.get("model")),
        )
        return TeamAgentContext(
            identity=_identity(profile),
            system_prompt=str(profile.get("system_prompt") or "").strip()
            or CODE_DEFAULT_AGENT_PROMPT.strip(),
            model=ModelOverride.from_profile(profile.get("model")),
            capabilities=AgentCapabilities.from_profile(profile),
            group_context=context,
            member_context=GroupMemberContext(
                memberships=memberships,
                active_group_id=str(group["group_id"]),
            ),
        )

    async def build_member(
        self,
        agent_id: str,
        group: dict[str, Any],
        *,
        profile_map: dict[str, dict[str, Any]] | None = None,
    ) -> TeamAgentContext:
        """按 agent_id 构建某个组成员的上下文（成员本人视角）。"""
        profiles = profile_map or {item["id"]: item for item in await self._profiles.list()}
        profile = profiles.get(agent_id)
        if profile is None:
            raise ValueError(f"Agent 不存在: {agent_id}")
        memberships = _memberships(agent_id, await self._groups.groups_of(agent_id))
        return await self.build_within(
            profile, group, memberships=memberships, profile_map=profiles
        )

    async def _resolve_profile(self, agent_id: str) -> dict[str, Any]:
        profile = await self._profiles.get(agent_id)
        if profile is None or not bool(profile.get("enabled", True)):
            profile = await self._profiles.get(DEFAULT_AGENT_ID)
        if profile is None:
            raise ValueError("用户没有任何可用 Agent")
        return profile

    async def _enrich_cards(
        self,
        group: dict[str, Any],
        *,
        profile_map: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[AgentCard, ...]:
        """用档案补全成员姓名与角色；档案被删时回退到 agent_id。"""
        profiles = profile_map or {
            item["id"]: item for item in await self._profiles.list()
        }
        cards: list[AgentCard] = []
        for member in group.get("members") or []:
            agent_id = str(member.get("agent_id") or "")
            if not agent_id:
                continue
            profile = profiles.get(agent_id) or {}
            cards.append(
                AgentCard(
                    agent_id=agent_id,
                    name=str(profile.get("name") or agent_id),
                    role_title=str(profile.get("role_title") or ""),
                    description=str(
                        profile.get("description") or member.get("duty") or ""
                    ),
                )
            )
        return tuple(cards)
