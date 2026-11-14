"""团队工具：组发现、组概览与跨组委派。

所有工具在闭包里捕获 ``TeamAgentContext`` 与 ``TeamDeps``，不读全局状态。
返回统一为 JSON 字符串，错误也结构化返回，不把异常穿透给模型。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime, tool

from agent.agent_profiles import AgentProfileStore
from agent.agent_team.context import TeamAgentContext
from agent.agent_team.runner import GroupRunner, TeamDeps


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _cards(group: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for member in group.get("members") or []:
        agent_id = str(member.get("agent_id") or "")
        if not agent_id:
            continue
        profile = profiles.get(agent_id) or {}
        cards.append(
            {
                "agent_id": agent_id,
                "name": str(profile.get("name") or agent_id),
                "role": str(member.get("role") or "member"),
                "role_title": str(profile.get("role_title") or ""),
                "description": str(profile.get("description") or member.get("duty") or ""),
            }
        )
    return cards


def create_team_tools(context: TeamAgentContext, deps: TeamDeps) -> list[Any]:
    """按角色创建团队工具；组员只拿到查看类工具。"""
    assert context.group_context is not None and context.member_context is not None
    active = context.group_context.active_group
    member = context.member_context

    @tool
    def list_my_groups() -> str:
        """列出你所属的全部组、角色与当前生效组。

        切换生效组需要用户操作，你不能自行切换。仅在需要确认自己在哪些组时调用。
        """
        items = [
            {
                "group_id": item.group_id,
                "role": item.role.value,
                "duty": item.duty,
                "active": item.group_id == member.active_group_id,
            }
            for item in member.memberships
        ]
        return _json(
            {
                "ok": True,
                "active_group_id": member.active_group_id,
                "items": items,
            }
        )

    @tool
    async def get_group_overview(group_id: str = "") -> str:
        """查看你所属组的概述与成员名片；缺省为当前生效组。

        只能查看自己所属的组。需要跨组信息时使用 discover_agent_groups（仅组长）。
        """
        target = group_id or member.active_group_id
        if member.role_in(target) is None:
            return _json(
                {
                    "ok": False,
                    "error": "not_member",
                    "detail": "只能查看自己所属的组",
                }
            )
        if target == member.active_group_id:
            return _json(
                {
                    "ok": True,
                    "group": {
                        "group_id": active.group_id,
                        "name": active.name,
                        "summary": active.summary,
                    },
                    "members": [
                        {
                            "agent_id": card.agent_id,
                            "name": card.name,
                            "role": (
                                "leader"
                                if card.agent_id == active.leader_agent_id
                                else "member"
                            ),
                            "role_title": card.role_title,
                            "description": card.description,
                        }
                        for card in context.group_context.members
                    ],
                }
            )
        group = await deps.group_store.get(target)
        if group is None:
            return _json({"ok": False, "error": "group_not_found", "detail": target})
        profiles = {item["id"]: item for item in await AgentProfileStore(deps.store, deps.user_id).list()}
        return _json(
            {
                "ok": True,
                "group": {
                    "group_id": group["group_id"],
                    "name": group["name"],
                    "summary": group.get("summary", ""),
                },
                "members": _cards(group, profiles),
            }
        )

    tools: list[Any] = [list_my_groups, get_group_overview]
    if not member.is_active_leader:
        return tools

    @tool
    async def discover_agent_groups(query: str = "", limit: int = 10) -> str:
        """搜索可协作的其它组（仅组长可用）。

        返回组名、概述、组长与成员数，不返回成员细节。目标组必须在组管理里
        设为「可发现」，否则不会出现在结果里。
        """
        groups = await deps.group_store.list()
        keyword = query.strip().lower()
        items = [
            {
                "group_id": group["group_id"],
                "name": group["name"],
                "summary": group.get("summary", ""),
                "leader_agent_id": group.get("leader_agent_id", ""),
                "member_count": len(group.get("members") or []),
            }
            for group in groups
            if group.get("visibility") == "discoverable"
            and group["group_id"] != active.group_id
            and (
                not keyword
                or keyword in str(group.get("name", "")).lower()
                or keyword in str(group.get("summary", "")).lower()
            )
        ][: max(1, min(int(limit or 10), 20))]
        return _json({"ok": True, "items": items})

    @tool
    async def delegate_to_group_leader(
        group_id: str,
        task: str,
        runtime: ToolRuntime,
        expected_output: str = "",
    ) -> str:
        """把一段边界清晰的子任务委派给其它组的组长，并等待其返回结论。

        使用条件：你是当前组组长；目标组存在且对组长可见；目标组不是当前组；
        跨组深度不超过上限；同一组不能在委派链中重复出现。
        """
        ctx = getattr(runtime, "context", None) if runtime is not None else None
        if str(getattr(ctx, "group_id", "") or "") != active.group_id:
            return _json(
                {
                    "ok": False,
                    "error": "stale_context",
                    "detail": "工具实例的组上下文与运行时不一致",
                }
            )
        runner = GroupRunner(deps)
        depth = int(getattr(ctx, "team_depth", 0) or 0)
        chain = tuple(getattr(ctx, "delegation_chain", ()) or ())
        return _json(
            await runner.delegate(
                context,
                group_id,
                task,
                expected_output,
                depth=depth,
                chain=chain,
            )
        )

    tools.extend([discover_agent_groups, delegate_to_group_leader])
    return tools
