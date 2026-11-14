"""AgentGroupStore 的单元自检（无需 pytest / 数据库）。

运行：python test/agent_team/test_store.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from agent.agent_team.context import GroupRole  # noqa: E402
from agent.agent_team.store import AgentGroupStore  # noqa: E402
from _fakes import FakeStore  # noqa: E402


async def main() -> None:
    store = FakeStore()
    groups = AgentGroupStore(store, "u1")

    # 1. 创建：组长自动进入成员列表并获得 leader 角色
    group = await groups.create(
        {
            "name": "数据分析组",
            "summary": "清洗与指标",
            "leader_agent_id": "agent_a",
            "members": [{"agent_id": "agent_b", "duty": "清洗"}],
            "visibility": "discoverable",
            "extra_subagent_names": ["web_researcher", "web_researcher", " "],
            "model": {"model_name": "group-model", "thinking_enabled": False},
        }
    )
    group_id = group["group_id"]
    assert group_id.startswith("grp_")
    assert [m["agent_id"] for m in group["members"]] == ["agent_a", "agent_b"]
    assert group["members"][0]["role"] == GroupRole.LEADER.value
    assert group["extra_subagent_names"] == ["web_researcher"]
    assert group["model"]["thinking_enabled"] is False

    # 2. 组名唯一
    try:
        await groups.create({"name": "数据分析组", "leader_agent_id": "agent_a"})
    except ValueError as error:
        assert "组名已存在" in str(error)
    else:
        raise AssertionError("重名组应被拒绝")

    # 3. 读取与反查索引
    assert (await groups.get(group_id))["name"] == "数据分析组"
    assert await groups.get("bad-id") is None
    alice_groups = await groups.groups_of("agent_a")
    assert [g["group_id"] for g in alice_groups] == [group_id]
    assert [g["group_id"] for g in await groups.leader_groups_of("agent_a")] == [group_id]
    assert await groups.leader_groups_of("agent_b") == []

    # 4. 重复加成员是幂等更新，不产生重复项
    updated = await groups.add_member(group_id, "agent_b", duty="清洗 + 校验")
    assert len(updated["members"]) == 2
    bob = next(m for m in updated["members"] if m["agent_id"] == "agent_b")
    assert bob["duty"] == "清洗 + 校验"

    # 5. 组长不能直接移出组
    try:
        await groups.remove_member(group_id, "agent_a")
    except ValueError as error:
        assert "先转移组长" in str(error)
    else:
        raise AssertionError("移出组长应被拒绝")

    # 6. 转移组长：旧组长降级、新组长升级（同一次写入）
    transferred = await groups.transfer_leader(group_id, "agent_b")
    assert transferred["leader_agent_id"] == "agent_b"
    roles = {m["agent_id"]: m["role"] for m in transferred["members"]}
    assert roles["agent_b"] == GroupRole.LEADER.value
    assert roles["agent_a"] == GroupRole.MEMBER.value
    assert [g["group_id"] for g in await groups.leader_groups_of("agent_b")] == [group_id]

    # 7. 修改组属性与组模型
    patched = await groups.update_group(
        group_id,
        {
            "summary": "新概述",
            "visibility": "private",
            "extra_subagent_names": [],
            "model": {},
        },
    )
    assert patched["summary"] == "新概述"
    assert patched["visibility"] == "private"
    assert patched["extra_subagent_names"] == []
    assert patched["model"] == {}

    # 8. 删除组后索引同步清空
    assert await groups.delete(group_id) is True
    assert await groups.delete(group_id) is False
    assert await groups.groups_of("agent_a") == []
    assert await groups.groups_of("agent_b") == []

    # 9. 非法可见性回落为 private，避免脏枚举进提示词
    fallback = await groups.create(
        {
            "name": "兜底组",
            "leader_agent_id": "agent_c",
            "visibility": "some-invalid-value",
        }
    )
    assert fallback["visibility"] == "private"

    # 10. 允许无队长创建，之后设置并取消队长
    leaderless = await groups.create(
        {"name": "无队长组", "members": [{"agent_id": "agent_d"}]}
    )
    assert leaderless["leader_agent_id"] == ""
    assert all(
        member["role"] == GroupRole.MEMBER.value for member in leaderless["members"]
    )
    appointed = await groups.transfer_leader(leaderless["group_id"], "agent_d")
    assert appointed["leader_agent_id"] == "agent_d"
    cleared = await groups.clear_leader(leaderless["group_id"])
    assert cleared["leader_agent_id"] == ""
    assert all(
        member["role"] == GroupRole.MEMBER.value for member in cleared["members"]
    )

    print("test_store: ok")


if __name__ == "__main__":
    asyncio.run(main())
