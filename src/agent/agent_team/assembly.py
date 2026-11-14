"""组图装配：成员子 Agent、可见性与团队权限中间件。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.agent_profiles import AgentProfileStore
from agent.agent_team.context import AgentMode, HarnessAgentContextBase, TeamAgentContext
from agent.agent_team.factory import AgentContextFactory
from agent.agent_team.middleware import TeamPermissionMiddleware
from agent.agent_team.store import AgentGroupStore
from agent.config import build_model
from agent.middlewares.model_concurrency import ModelConcurrencyMiddleware
from agent.middlewares.subagent_progress import SubagentProgressMiddleware
from agent.middlewares.time_awareness import TimeAwarenessMiddleware
from agent.user_data import model_settings

logger = logging.getLogger(__name__)


async def build_member_subagents(
    *,
    context: TeamAgentContext,
    store: Any,
    user_id: str,
    sandbox_backend: Any,
    local_tools: list[Any],
) -> list[dict[str, Any]]:
    """把组成员转换为 DeepAgents 声明式子 Agent。"""
    assert context.group_context is not None
    group = context.group_context.active_group
    group_record = await AgentGroupStore(store, user_id).get(group.group_id)
    if group_record is None:
        logger.warning("组记录不存在，跳过成员装配：%s", group.group_id)
        return []

    profile_map = {item["id"]: item for item in await AgentProfileStore(store, user_id).list()}
    factory = AgentContextFactory(store, user_id)

    subagents: list[dict[str, Any]] = []
    for card in context.group_context.members:
        # 组长就是当前主 Agent，不能把自己再注册成子 Agent
        if card.agent_id == context.identity.agent_id:
            continue
        profile = profile_map.get(card.agent_id)
        if profile is None or not bool(profile.get("enabled", True)):
            logger.warning("组成员档案缺失或停用，已跳过：%s", card.agent_id)
            continue
        member_context = await factory.build_member(
            card.agent_id, group_record, profile_map=profile_map
        )
        subagents.append(
            {
                # 子 Agent 名用 agent_id：稳定、ASCII、task 工具可直接使用
                "name": card.agent_id,
                "description": (
                    f"{profile.get('name')}｜{profile.get('role_title')}："
                    f"{profile.get('description') or card.description or '组成员'}"
                ),
                "system_prompt": (
                    f"{member_context.render()}\n\n"
                    "## 委派边界\n"
                    f"- 你只服务于组「{group.name}」，任务来自组长。\n"
                    "- 不要向其他成员横向委派，也不要尝试管理组结构。\n"
                    "- 完成后直接给出结论，不要输出过程性思考。\n"
                ),
                "tools": await _member_tools(profile, local_tools),
                "model": build_model(
                    member_context.resolve_model(model_settings(user_id)),
                    role="member",
                    user_id=user_id,
                ),
                "middleware": [
                    # 与全局子 Agent 共用同一个模型调用闸门。
                    ModelConcurrencyMiddleware(),
                    SubagentProgressMiddleware(card.agent_id),
                    # 组员没有任何团队工具；中间件负责"看不见 + 调不动"
                    TeamPermissionMiddleware(
                        agent_id=card.agent_id,
                        group_id=group.group_id,
                        allowed=frozenset(),
                    ),
                    # 组员的入口消息来自组长委派，需要入口补时间戳；放在最内层
                    # 保证时间前缀渲染在最终发给模型的消息上。
                    TimeAwarenessMiddleware(stamp_missing_human=True),
                ],
            }
        )
    return subagents


async def build_team_subagents(
    *,
    context: TeamAgentContext,
    store: Any,
    user_id: str,
    sandbox_backend: Any,
    local_tools: list[Any],
    load_global_subagents: Callable[[], Awaitable[list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """团队模式的子 Agent 清单 = 本组成员 + 组白名单命中的全局 YAML。

    这是"职责边界"的落点：未勾选的全局 YAML 不会进入 task 描述，
    组长无法把任务委派给组外的人。
    """
    assert context.member_context is not None
    group_record = await AgentGroupStore(store, user_id).get(
        context.member_context.active_group_id
    )
    allowed = set((group_record or {}).get("extra_subagent_names") or [])

    globals_ = await load_global_subagents()
    known = {str(item.get("name") or "") for item in globals_}
    unknown = sorted(name for name in allowed if name and name not in known)
    if unknown:
        # 只告警不阻断：YAML 可能被临时移除，整个组不应因此不可用
        logger.warning(
            "组 %s 配置了未知子 Agent，已忽略：%s",
            context.member_context.active_group_id,
            unknown,
        )

    extras = [item for item in globals_ if str(item.get("name") or "") in allowed]
    members = await build_member_subagents(
        context=context,
        store=store,
        user_id=user_id,
        sandbox_backend=sandbox_backend,
        local_tools=local_tools,
    )
    return [*members, *extras]


async def _member_tools(profile: dict[str, Any], local_tools: list[Any]) -> list[Any]:
    """成员工具 = 自己的 MCP 服务工具 + 基础本地工具。"""
    tools = list(local_tools)
    server_ids = [str(item) for item in (profile.get("mcp_server_ids") or []) if item]
    if server_ids:
        from agent.mcp.runtime import mcp_runtime

        tools.extend(await mcp_runtime.tools_for_servers(server_ids))
    return tools


def _assert_unique_subagent_names(subagents: list[dict[str, Any]]) -> None:
    """组成员与 YAML 子 Agent 合并后必须全局唯一，冲突直接失败。"""
    names = [str(item.get("name") or "") for item in subagents]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"子 Agent 名称重复: {duplicates}")


def team_runtime_middleware(context: HarnessAgentContextBase) -> list[Any]:
    """主 Agent 的团队权限中间件；普通 Agent 不挂。"""
    if context.mode is not AgentMode.TEAM:
        return []
    assert context.member_context is not None
    return [
        TeamPermissionMiddleware(
            agent_id=context.identity.agent_id,
            group_id=context.member_context.active_group_id,
            allowed=context.allowed_team_tools(),
        )
    ]
