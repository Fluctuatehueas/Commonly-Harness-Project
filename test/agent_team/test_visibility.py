"""团队可见性边界与权限中间件的单元自检。

运行：python test/agent_team/test_visibility.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_MODEL_NAME", "test-model")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from agent.agent_profiles import AgentProfileStore  # noqa: E402
from agent.agent_team.assembly import (  # noqa: E402
    build_team_subagents,
    team_runtime_middleware,
)
from agent.agent_team.context import AgentMode  # noqa: E402
from agent.agent_team.factory import AgentContextFactory  # noqa: E402
from agent.agent_team.middleware import TeamPermissionMiddleware  # noqa: E402
from agent.agent_team.store import AgentGroupStore  # noqa: E402
from _fakes import FakeStore  # noqa: E402

GLOBAL_SUBAGENTS = [
    {"name": "web_researcher", "description": "联网检索"},
    {"name": "document_reader", "description": "读文档"},
]


async def main() -> None:
    store = FakeStore()
    profiles = AgentProfileStore(store, "u1")
    await profiles.create(
        {"id": "agent_a", "name": "小张", "role_title": "组长", "system_prompt": "A"}
    )
    await profiles.create(
        {"id": "agent_b", "name": "小李", "role_title": "清洗员", "system_prompt": "B"}
    )
    groups = AgentGroupStore(store, "u1")
    group = await groups.create(
        {
            "name": "数据分析组",
            "leader_agent_id": "agent_a",
            "members": [{"agent_id": "agent_b"}],
            "extra_subagent_names": ["web_researcher"],
        }
    )
    factory = AgentContextFactory(store, "u1")
    context = await factory.build("agent_a", group["group_id"])

    async def load_globals() -> list[dict]:
        return [dict(item) for item in GLOBAL_SUBAGENTS]

    subagents = await build_team_subagents(
        context=context,
        store=store,
        user_id="u1",
        sandbox_backend=None,
        local_tools=[],
        load_global_subagents=load_globals,
    )
    names = [item["name"] for item in subagents]
    # 本组成员（不含组长自己）+ 白名单命中的全局 YAML；未命中的全局 YAML 不可见
    assert names == ["agent_b", "web_researcher"], names

    # 未勾选时组长只能看到本组成员
    await groups.update_group(group["group_id"], {"extra_subagent_names": []})
    subagents = await build_team_subagents(
        context=context,
        store=store,
        user_id="u1",
        sandbox_backend=None,
        local_tools=[],
        load_global_subagents=load_globals,
    )
    assert [item["name"] for item in subagents] == ["agent_b"]

    # 未知名字只告警、不阻断
    await groups.update_group(
        group["group_id"], {"extra_subagent_names": ["not_exists"]}
    )
    subagents = await build_team_subagents(
        context=context,
        store=store,
        user_id="u1",
        sandbox_backend=None,
        local_tools=[],
        load_global_subagents=load_globals,
    )
    assert [item["name"] for item in subagents] == ["agent_b"]

    # 组长有跨组工具，组员没有
    leader_mw = team_runtime_middleware(context)
    assert len(leader_mw) == 1
    assert not leader_mw[0]._denied("delegate_to_group_leader")
    member = await factory.build_member("agent_b", group)
    member_mw = team_runtime_middleware(member)
    assert member_mw[0]._denied("delegate_to_group_leader")
    assert member_mw[0]._denied("discover_agent_groups")

    # 模型可见工具过滤：团队工具被剔除，普通工具保留
    filtered = member_mw[0]._filter(
        [{"name": "delegate_to_group_leader"}, {"name": "read_file"}]
    )
    assert filtered == [{"name": "read_file"}]

    # 普通 Agent 不挂团队中间件
    standalone = await factory.build("agent_a", None)
    assert standalone.mode is AgentMode.STANDALONE
    assert team_runtime_middleware(standalone) == []

    # 独立 Agent 的权限中间件不应误伤普通工具
    guard = TeamPermissionMiddleware(
        agent_id="agent_x", group_id="grp_1234", allowed=frozenset()
    )
    assert guard._filter([{"name": "read_file"}]) == [{"name": "read_file"}]

    print("test_visibility: ok")


if __name__ == "__main__":
    asyncio.run(main())
