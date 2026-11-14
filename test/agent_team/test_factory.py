"""AgentContextFactory 的单元自检（无需 pytest / 数据库）。

运行：python test/agent_team/test_factory.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from agent.agent_profiles import AgentProfileStore  # noqa: E402
from agent.agent_team.context import AgentMode  # noqa: E402
from agent.agent_team.factory import (  # noqa: E402
    AgentContextFactory,
    GroupNotFoundError,
    NotGroupMemberError,
)
from agent.agent_team.store import AgentGroupStore  # noqa: E402
from _fakes import FakeStore  # noqa: E402

USER_DEFAULTS = {
    "model_name": "user-default",
    "base_url": "https://user.example",
    "api_key": "user-key",
    "thinking_enabled": True,
    "context_limit": 128000,
}


async def main() -> None:
    store = FakeStore()
    profiles = AgentProfileStore(store, "u1")
    await profiles.create(
        {
            "id": "agent_a",
            "name": "小张",
            "role_title": "组长",
            "description": "统筹分工",
            "system_prompt": "你是组长。",
            "model": {"model_name": "agent-model"},
        }
    )
    await profiles.create(
        {
            "id": "agent_b",
            "name": "小李",
            "role_title": "清洗员",
            "description": "脏数据清洗",
            "system_prompt": "你是清洗员。",
        }
    )
    groups = AgentGroupStore(store, "u1")
    group = await groups.create(
        {
            "name": "数据分析组",
            "summary": "清洗与指标",
            "leader_agent_id": "agent_a",
            "members": [{"agent_id": "agent_b", "duty": "清洗"}],
            "model": {"model_name": "group-model"},
        }
    )
    group_id = group["group_id"]

    factory = AgentContextFactory(store, "u1")

    # 1. 无生效组 → 普通 Agent，提示词与能力保持原样
    standalone = await factory.build("agent_a", None)
    assert standalone.mode is AgentMode.STANDALONE
    assert standalone.group_context is None
    assert standalone.allowed_team_tools() == frozenset()
    assert "你是组长。" in standalone.render()

    # 2. 组长上下文：角色、成员、组模型、可发现组
    other = {
        "group_id": "grp_other1",
        "name": "报告组",
        "summary": "对外成稿",
        "leader_agent_id": "agent_c",
        "members": [],
        "visibility": "discoverable",
    }
    leader = await factory.build_within(
        (await profiles.get("agent_a")), group, discoverable=[other]
    )
    assert leader.mode is AgentMode.TEAM
    assert leader.is_active_leader
    assert "delegate_to_group_leader" in leader.allowed_team_tools()
    prompt = leader.render()
    assert "数据分析组" in prompt
    assert "agent_b（小李）" in prompt
    assert "报告组" in prompt
    assert leader.group_context is not None
    assert len(leader.group_context.discoverable_groups) == 1

    # 3. 组员上下文：看不到其他组，也没有跨组工具
    member = await factory.build_member("agent_b", group)
    assert member.mode is AgentMode.TEAM
    assert not member.is_active_leader
    assert member.group_context.discoverable_groups == ()
    assert "delegate_to_group_leader" not in member.allowed_team_tools()
    assert "discover_agent_groups" not in member.allowed_team_tools()

    # 4. 模型三级继承：组长有自己的模型，组员用组模型
    leader_model = leader.resolve_model(USER_DEFAULTS)
    assert leader_model["model_name"] == "agent-model"
    assert leader_model["api_key"] == "user-key"
    member_model = member.resolve_model(USER_DEFAULTS)
    assert member_model["model_name"] == "group-model"
    assert member_model["base_url"] == "https://user.example"

    # 5. 不在组里 → 明确报错，不静默降级
    default_agent = await profiles.ensure_default()
    try:
        await factory.build(default_agent["id"], group_id)
    except NotGroupMemberError:
        pass
    else:
        raise AssertionError("非成员应抛 NotGroupMemberError")

    # 6. 组不存在 → 明确报错
    try:
        await factory.build("agent_a", "grp_none1")
    except GroupNotFoundError:
        pass
    else:
        raise AssertionError("不存在的组应抛 GroupNotFoundError")

    # 7. 成员视角也能反查自己的多组籍
    group2 = await groups.create(
        {"name": "第二组", "leader_agent_id": "agent_b", "members": []}
    )
    member_again = await factory.build_member("agent_b", group2)
    assert set(member_again.member_context.leader_group_ids) == {group2["group_id"]}
    assert group_id in member_again.member_context.member_group_ids

    print("test_factory: ok")


if __name__ == "__main__":
    asyncio.run(main())
