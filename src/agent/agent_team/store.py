"""AgentTeam 组的持久化。

组记录是唯一事实来源；``agent_memberships`` 只是反向查询索引，
任何时候都可以由组记录全量重建。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from agent.agent_profiles import normalize_model
from agent.agent_team.context import GroupRole, GroupVisibility

logger = logging.getLogger(__name__)

GROUP_NAMESPACE_PREFIX = ("agent_groups",)
MEMBERSHIP_INDEX_PREFIX = ("agent_memberships",)
GROUP_ID_PATTERN = re.compile(r"^grp_[A-Za-z0-9]{4,32}$")
MAX_MEMBERS_PER_GROUP = 50
MAX_GROUPS_PER_USER = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_names(raw: Any) -> list[str]:
    """去重保序的子 Agent 名单；未知名字留到构图时告警，不在写库阶段阻断。"""
    names: list[str] = []
    for item in list(raw or []):
        value = str(item or "").strip()[:64]
        if value and value not in names:
            names.append(value)
    return names


def _normalize_visibility(raw: Any) -> str:
    try:
        return GroupVisibility(str(raw or "")).value
    except ValueError:
        return GroupVisibility.PRIVATE.value


class AgentGroupStore:
    """按用户读写组；写操作由调用方在用户级锁内串行。"""

    def __init__(self, store: Any, user_id: str) -> None:
        self._store = store
        self._user_id = user_id
        self._groups_ns = (*GROUP_NAMESPACE_PREFIX, user_id)
        self._index_ns = (*MEMBERSHIP_INDEX_PREFIX, user_id)

    # ---------- 读 ----------

    async def list(self) -> list[dict[str, Any]]:
        items = await self._store.asearch(self._groups_ns, limit=MAX_GROUPS_PER_USER)
        groups = [dict(item.value) for item in items]
        groups.sort(key=lambda group: (group.get("name", ""), group.get("group_id", "")))
        return groups

    async def get(self, group_id: str) -> dict[str, Any] | None:
        if not GROUP_ID_PATTERN.fullmatch(str(group_id or "")):
            return None
        item = await self._store.aget(self._groups_ns, group_id)
        return dict(item.value) if item is not None else None

    async def groups_of(self, agent_id: str) -> list[dict[str, Any]]:
        """反查某 Agent 的全部组；索引缺失时回退全量扫描并修复。"""
        item = await self._store.aget(self._index_ns, agent_id)
        if item is not None:
            ids = list((dict(item.value) or {}).get("group_ids") or [])
            groups: list[dict[str, Any]] = []
            for group_id in ids:
                group = await self.get(group_id)
                if group is not None:
                    groups.append(group)
            return groups

        groups = [
            group
            for group in await self.list()
            if any(member.get("agent_id") == agent_id for member in group.get("members") or [])
        ]
        await self._refresh_index([agent_id])
        return groups

    async def leader_groups_of(self, agent_id: str) -> list[dict[str, Any]]:
        return [
            group
            for group in await self.groups_of(agent_id)
            if group.get("leader_agent_id") == agent_id
        ]

    # ---------- 写 ----------

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("组名不能为空")
        leader_id = str(payload.get("leader_agent_id") or "").strip()

        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload.get("members") or []:
            agent_id = str(item.get("agent_id") or "").strip()
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            members.append(
                {
                    "agent_id": agent_id,
                    "role": (
                        GroupRole.LEADER.value
                        if leader_id and agent_id == leader_id
                        else GroupRole.MEMBER.value
                    ),
                    "duty": str(item.get("duty") or "")[:200],
                    "joined_at": _now(),
                }
            )
        if leader_id and leader_id not in seen:
            members.insert(
                0,
                {
                    "agent_id": leader_id,
                    "role": GroupRole.LEADER.value,
                    "duty": "队长",
                    "joined_at": _now(),
                },
            )
        if len(members) > MAX_MEMBERS_PER_GROUP:
            raise ValueError(f"组成员超过上限 {MAX_MEMBERS_PER_GROUP}")
        if len(await self.list()) >= MAX_GROUPS_PER_USER:
            raise ValueError(f"组数量超过上限 {MAX_GROUPS_PER_USER}")

        await self._assert_name_unique(name)
        group = {
            "group_id": f"grp_{uuid.uuid4().hex[:8]}",
            "name": name,
            "summary": str(payload.get("summary") or "")[:500],
            "leader_agent_id": leader_id,
            "members": members,
            "visibility": _normalize_visibility(payload.get("visibility")),
            # 团队模式下额外可见的全局 YAML 子 Agent；空表示只见本组成员
            "extra_subagent_names": _normalize_names(payload.get("extra_subagent_names")),
            # 组级模型策略：空 dict 表示不提供，成员继续继承用户默认
            "model": normalize_model(payload.get("model")),
            "created_at": _now(),
            "updated_at": _now(),
        }
        await self._store.aput(self._groups_ns, group["group_id"], group, index=False)
        await self._refresh_index([member["agent_id"] for member in members])
        return group

    async def update_group(self, group_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """修改组名 / 概述 / 可见性 / 组模型 / 额外子 Agent 名单。

        成员与组长有专用方法，这里不处理，避免绕过角色不变量。
        """
        group = await self._require(group_id)
        updated = dict(group)
        if "name" in patch:
            name = str(patch.get("name") or "").strip()[:80]
            if not name:
                raise ValueError("组名不能为空")
            await self._assert_name_unique(name, exclude=group_id)
            updated["name"] = name
        if "summary" in patch:
            updated["summary"] = str(patch.get("summary") or "")[:500]
        if "visibility" in patch:
            updated["visibility"] = _normalize_visibility(patch.get("visibility"))
        if "extra_subagent_names" in patch:
            updated["extra_subagent_names"] = _normalize_names(
                patch.get("extra_subagent_names")
            )
        if "model" in patch:
            # 前端回传空对象表示"取消组模型，成员回落到用户默认模型"
            updated["model"] = normalize_model(patch.get("model"))
        updated["updated_at"] = _now()
        await self._store.aput(self._groups_ns, group_id, updated, index=False)
        return updated

    async def delete(self, group_id: str) -> bool:
        group = await self.get(group_id)
        if group is None:
            return False
        await self._store.adelete(self._groups_ns, group_id)
        await self._refresh_index(
            [member["agent_id"] for member in group.get("members") or []]
        )
        return True

    async def add_member(
        self, group_id: str, agent_id: str, duty: str = ""
    ) -> dict[str, Any]:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent_id 不能为空")
        group = await self._require(group_id)
        members = list(group.get("members") or [])
        for member in members:
            if member["agent_id"] == agent_id:
                # 幂等：已存在时只更新职责，不产生重复组籍
                member["duty"] = str(duty or member.get("duty") or "")[:200]
                return await self._save_members(group, members)
        if len(members) >= MAX_MEMBERS_PER_GROUP:
            raise ValueError("组成员已达上限")
        members.append(
            {
                "agent_id": agent_id,
                "role": GroupRole.MEMBER.value,
                "duty": str(duty or "")[:200],
                "joined_at": _now(),
            }
        )
        return await self._save_members(group, members)

    async def remove_member(self, group_id: str, agent_id: str) -> dict[str, Any]:
        group = await self._require(group_id)
        if group.get("leader_agent_id") == agent_id:
            raise ValueError("组长不能直接移出组，请先转移组长")
        members = [
            member
            for member in group.get("members") or []
            if member["agent_id"] != agent_id
        ]
        if len(members) == len(group.get("members") or []):
            return group
        return await self._save_members(group, members, removed=[agent_id])

    async def transfer_leader(self, group_id: str, agent_id: str) -> dict[str, Any]:
        agent_id = str(agent_id or "").strip()
        group = await self._require(group_id)
        members = list(group.get("members") or [])
        if not any(member["agent_id"] == agent_id for member in members):
            raise ValueError("新组长必须是组内成员")
        for member in members:
            member["role"] = (
                GroupRole.LEADER.value
                if member["agent_id"] == agent_id
                else GroupRole.MEMBER.value
            )
        updated = dict(group)
        updated["leader_agent_id"] = agent_id
        return await self._save_members(updated, members)

    async def clear_leader(self, group_id: str) -> dict[str, Any]:
        """取消队长：组可以暂时无队长，成员全部回落为普通成员。"""
        group = await self._require(group_id)
        members = list(group.get("members") or [])
        for member in members:
            member["role"] = GroupRole.MEMBER.value
        updated = dict(group)
        updated["leader_agent_id"] = ""
        return await self._save_members(updated, members)

    # ---------- 内部 ----------

    async def _require(self, group_id: str) -> dict[str, Any]:
        group = await self.get(group_id)
        if group is None:
            raise ValueError(f"组不存在: {group_id}")
        return group

    async def _save_members(
        self,
        group: dict[str, Any],
        members: list[dict[str, Any]],
        *,
        removed: Iterable[str] = (),
    ) -> dict[str, Any]:
        updated = dict(group)
        updated["members"] = members
        updated["updated_at"] = _now()
        await self._store.aput(self._groups_ns, updated["group_id"], updated, index=False)
        affected = {member["agent_id"] for member in members} | set(removed)
        await self._refresh_index(list(affected))
        return updated

    async def _assert_name_unique(self, name: str, exclude: str = "") -> None:
        for group in await self.list():
            if group.get("name") == name and group.get("group_id") != exclude:
                raise ValueError(f"组名已存在: {name}")

    async def _refresh_index(self, agent_ids: list[str]) -> None:
        """按组记录重建受影响 Agent 的派生索引（幂等）。"""
        groups = await self.list()
        for agent_id in {value for value in agent_ids if value}:
            joined = [
                group["group_id"]
                for group in groups
                if any(
                    member.get("agent_id") == agent_id
                    for member in group.get("members") or []
                )
            ]
            leader_of = [
                group["group_id"]
                for group in groups
                if group.get("leader_agent_id") == agent_id
            ]
            await self._store.aput(
                self._index_ns,
                agent_id,
                {
                    "group_ids": joined,
                    "leader_of": leader_of,
                    "updated_at": _now(),
                },
                index=False,
            )
