"""上下文对象与不变量的单元自检（无需 pytest）。

运行：python test/agent_team/test_context.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent.agent_team.context import (  # noqa: E402
    AgentCapabilities,
    AgentCard,
    AgentIdentity,
    AgentMode,
    GroupContext,
    GroupDescriptor,
    GroupMemberContext,
    GroupMembership,
    GroupRole,
    HarnessAgentContextBase,
    StandaloneAgentContext,
    TeamAgentContext,
)


def _team_context() -> TeamAgentContext:
    leader = AgentIdentity(agent_id="agent_a", name="小张", role_title="组长")
    group = GroupDescriptor(
        group_id="grp_1234",
        name="数据分析组",
        summary="负责数据清洗与指标计算",
        leader_agent_id="agent_a",
        member_count=2,
    )
    return TeamAgentContext(
        identity=leader,
        system_prompt="你是数据分析组组长。",
        group_context=GroupContext(
            active_group=group,
            members=(
                leader.to_card(),
                AgentCard(agent_id="agent_b", name="小李", role_title="清洗员"),
            ),
        ),
        member_context=GroupMemberContext(
            memberships=(GroupMembership(group_id="grp_1234", role=GroupRole.LEADER),),
            active_group_id="grp_1234",
        ),
    )


def main() -> None:
    # 1. 普通 Agent 不允许携带组字段
    try:
        StandaloneAgentContext(
            identity=AgentIdentity(agent_id="agent_a", name="小张"),
            system_prompt="x",
            group_context=_team_context().group_context,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("StandaloneAgentContext 应拒绝组上下文")

    # 2. 团队 Agent 必须同时提供两个组对象
    try:
        TeamAgentContext(
            identity=AgentIdentity(agent_id="agent_a", name="小张"),
            system_prompt="x",
            group_context=_team_context().group_context,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("TeamAgentContext 缺 member_context 应报错")

    # 3. 组长标记与组籍角色必须一致
    try:
        TeamAgentContext(
            identity=AgentIdentity(agent_id="agent_b", name="小李"),
            system_prompt="x",
            group_context=_team_context().group_context,
            member_context=GroupMemberContext(
                memberships=(GroupMembership(group_id="grp_1234", role=GroupRole.LEADER),),
                active_group_id="grp_1234",
            ),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("非组长被标记为 leader 时应报错")

    # 4. 渲染包含组名、角色、成员名片，且只渲染一次自己
    context = _team_context()
    prompt = context.render()
    assert "数据分析组" in prompt
    assert "你的角色：组长" in prompt
    assert "agent_b（小李）" in prompt
    assert "（你自己，不可委派）" in prompt
    assert "委派优先" in prompt
    # 组员视角不出现组长的委派入口说明
    member_view = context.group_context.render(
        self_agent_id="agent_b", role=GroupRole.MEMBER
    )
    assert "组长执行约束" not in member_view
    assert "task" not in member_view

    # 5. 工具集由角色多态决定
    assert "delegate_to_group_leader" in context.allowed_team_tools()
    member = TeamAgentContext(
        identity=AgentIdentity(agent_id="agent_b", name="小李"),
        system_prompt="x",
        group_context=context.group_context,
        member_context=GroupMemberContext(
            memberships=(GroupMembership(group_id="grp_1234", role=GroupRole.MEMBER),),
            active_group_id="grp_1234",
        ),
    )
    assert "delegate_to_group_leader" not in member.allowed_team_tools()
    assert member.mode is AgentMode.TEAM

    # 6. 空能力与空提示词不应产生多余分隔
    standalone = StandaloneAgentContext(
        identity=AgentIdentity(agent_id="agent_x", name="独立"),
        system_prompt="  ",
        capabilities=AgentCapabilities(),
    )
    assert standalone.render() == ""
    assert standalone.mode is AgentMode.STANDALONE
    assert standalone.allowed_team_tools() == frozenset()

    # 7. 基类不可直接实例化
    try:
        HarnessAgentContextBase(
            identity=AgentIdentity(agent_id="agent_x", name="独立"),
            system_prompt="x",
        )
    except TypeError:
        pass
    else:
        raise AssertionError("抽象基类不应可直接实例化")

    print("test_context: ok")


if __name__ == "__main__":
    main()
