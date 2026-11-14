"""模型三级继承的单元自检（无需 pytest）。

运行：python test/agent_team/test_model_resolution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent.agent_team.context import (  # noqa: E402
    AgentIdentity,
    GroupContext,
    GroupDescriptor,
    GroupMemberContext,
    GroupMembership,
    GroupRole,
    ModelOverride,
    StandaloneAgentContext,
    TeamAgentContext,
)

USER_DEFAULTS = {
    "model_name": "user-default",
    "base_url": "https://user.example",
    "api_key": "user-key",
    "thinking_enabled": True,
    "context_limit": 128000,
}


def _team(agent_model: ModelOverride, group_model: ModelOverride) -> TeamAgentContext:
    leader = AgentIdentity(agent_id="agent_a", name="小张")
    return TeamAgentContext(
        identity=leader,
        system_prompt="x",
        model=agent_model,
        group_context=GroupContext(
            active_group=GroupDescriptor(
                group_id="grp_1234", name="组", leader_agent_id="agent_a"
            ),
            members=(leader.to_card(),),
            model=group_model,
        ),
        member_context=GroupMemberContext(
            memberships=(GroupMembership(group_id="grp_1234", role=GroupRole.LEADER),),
            active_group_id="grp_1234",
        ),
    )


def main() -> None:
    # 1. 空对象不覆盖任何字段
    empty = ModelOverride()
    assert not empty.configured
    assert empty.merge(USER_DEFAULTS) == USER_DEFAULTS

    # 2. 组只配 model_name 时，其余字段继续继承用户默认
    group_only = ModelOverride(model_name="group-model")
    resolved = _team(ModelOverride(), group_only).resolve_model(USER_DEFAULTS)
    assert resolved["model_name"] == "group-model"
    assert resolved["api_key"] == "user-key"
    assert resolved["base_url"] == "https://user.example"

    # 3. Agent 配置覆盖组配置
    agent_model = ModelOverride(model_name="agent-model", context_limit=64000)
    resolved = _team(agent_model, group_only).resolve_model(USER_DEFAULTS)
    assert resolved["model_name"] == "agent-model"
    assert resolved["context_limit"] == 64000
    # 组配置的 model_name 被 Agent 覆盖后不应残留
    assert resolved["model_name"] != "group-model"

    # 4. thinking_enabled=False 可以显式关闭用户默认的 True
    agent_off = ModelOverride(thinking_enabled=False)
    assert agent_off.configured
    resolved = _team(agent_off, ModelOverride()).resolve_model(USER_DEFAULTS)
    assert resolved["thinking_enabled"] is False

    # 5. 普通 Agent 只受自身配置影响
    standalone = StandaloneAgentContext(
        identity=AgentIdentity(agent_id="agent_x", name="独立"),
        system_prompt="x",
        model=ModelOverride(model_name="solo"),
    )
    resolved = standalone.resolve_model(USER_DEFAULTS)
    assert resolved["model_name"] == "solo"
    assert resolved["thinking_enabled"] is True

    # 6. from_profile 保留"未配置"与"显式关闭"的差异
    assert ModelOverride.from_profile({}).thinking_enabled is None
    assert ModelOverride.from_profile({"thinking_enabled": False}).thinking_enabled is False
    assert ModelOverride.from_profile({"thinking_enabled": True}).thinking_enabled is True

    print("test_model_resolution: ok")


if __name__ == "__main__":
    main()
