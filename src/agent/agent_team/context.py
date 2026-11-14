"""AgentTeam 的定义期上下文对象。

这一层只做三件事：

1. 描述一个 Agent 是谁（身份、提示词、模型、能力）；
2. 描述它在组里看到什么（``GroupContext``）；
3. 描述它在组里能做什么（``GroupMemberContext``）。

不读数据库、不建模型、不碰 LangGraph，保证可以被单元测试直接实例化。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

MAX_MEMBER_CARDS = 20
MAX_DISCOVERABLE_CARDS = 10


# ============================================================
# 枚举
# ============================================================


class GroupRole(str, Enum):
    """组内角色。一个 Agent 的角色按组独立。"""

    LEADER = "leader"
    MEMBER = "member"


class GroupVisibility(str, Enum):
    """组对其他组的可见性。"""

    PRIVATE = "private"
    DISCOVERABLE = "discoverable"


class AgentMode(str, Enum):
    """Agent 的执行形态。"""

    STANDALONE = "standalone"
    TEAM = "team"


# ============================================================
# 值对象
# ============================================================


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """身份四要素 + 头像。"""

    agent_id: str
    name: str
    role_title: str = ""
    description: str = ""
    avatar: str = "A"

    def to_card(self) -> "AgentCard":
        return AgentCard(
            agent_id=self.agent_id,
            name=self.name,
            role_title=self.role_title,
            description=self.description,
        )


@dataclass(frozen=True, slots=True)
class AgentCard:
    """给其他 Agent 看的名片：不含提示词、模型与凭据。"""

    agent_id: str
    name: str
    role_title: str = ""
    description: str = ""

    def as_prompt_line(self) -> str:
        role = f"｜{self.role_title}" if self.role_title else ""
        desc = f"：{self.description}" if self.description else ""
        return f"- {self.agent_id}（{self.name}）{role}{desc}"


@dataclass(frozen=True, slots=True)
class ModelOverride:
    """模型覆盖项，Agent 与组共用。

    ``thinking_enabled`` 用 ``None`` 表示"未配置"、``False`` 表示"显式关闭"；
    两者必须区分，否则无法覆盖用户默认的开启状态。
    """

    model_name: str = ""
    base_url: str = ""
    api_key: str = ""
    thinking_enabled: bool | None = None
    context_limit: int = 0

    @property
    def configured(self) -> bool:
        """任一字段被显式配置即视为已配置；空对象等价于不提供这一层。"""
        return bool(
            self.model_name
            or self.base_url
            or self.api_key
            or self.thinking_enabled is not None
            or self.context_limit
        )

    def merge(self, defaults: Mapping[str, Any]) -> dict[str, Any]:
        """逐字段覆盖：只覆盖显式配置过的字段。"""
        merged = dict(defaults)
        if self.model_name:
            merged["model_name"] = self.model_name
        if self.base_url:
            merged["base_url"] = self.base_url
        if self.api_key:
            merged["api_key"] = self.api_key
        if self.thinking_enabled is not None:
            merged["thinking_enabled"] = self.thinking_enabled
        if self.context_limit:
            merged["context_limit"] = self.context_limit
        return merged

    @classmethod
    def from_profile(cls, raw: Mapping[str, Any] | None) -> "ModelOverride":
        data = dict(raw or {})
        thinking = data.get("thinking_enabled")
        try:
            context_limit = int(data.get("context_limit") or 0)
        except (TypeError, ValueError):
            context_limit = 0
        return cls(
            model_name=str(data.get("model_name") or ""),
            base_url=str(data.get("base_url") or ""),
            api_key=str(data.get("api_key") or ""),
            thinking_enabled=None if thinking is None else bool(thinking),
            context_limit=context_limit,
        )


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Agent 自己的工具与技能范围。"""

    mcp_server_ids: tuple[str, ...] = ()
    extra_tool_names: tuple[str, ...] = ()
    skill_scopes: tuple[str, ...] = ()

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any]) -> "AgentCapabilities":
        return cls(
            mcp_server_ids=_unique_tuple(profile.get("mcp_server_ids")),
            extra_tool_names=_unique_tuple(profile.get("extra_tool_names")),
            skill_scopes=_unique_tuple(profile.get("skill_scopes")),
        )


@dataclass(frozen=True, slots=True)
class GroupDescriptor:
    """组的公开静态描述，不含模型与凭据。"""

    group_id: str
    name: str
    summary: str = ""
    leader_agent_id: str = ""
    visibility: GroupVisibility = GroupVisibility.PRIVATE
    member_count: int = 0


@dataclass(frozen=True, slots=True)
class GroupMembership:
    """一个 Agent 在某个组里的身份。"""

    group_id: str
    role: GroupRole
    duty: str = ""
    joined_at: str = ""


# ============================================================
# 组投影
# ============================================================


@dataclass(frozen=True, slots=True)
class GroupContext:
    """当前生效组的结构视图：我在这个组里能看到谁、组用什么模型。

    ``model`` 只参与模型解析，不参与 ``render()``：组模型可能含凭据，
    绝不能渲染进提示词或日志。
    """

    active_group: GroupDescriptor
    members: tuple[AgentCard, ...] = ()
    discoverable_groups: tuple[GroupDescriptor, ...] = ()
    model: ModelOverride = field(default_factory=ModelOverride)

    def sibling(self, agent_id: str) -> AgentCard | None:
        return next((card for card in self.members if card.agent_id == agent_id), None)

    def render(
        self,
        *,
        self_agent_id: str = "",
        role: GroupRole | None = None,
        duty: str = "",
    ) -> str:
        """渲染进提示词的组信息段。"""
        group = self.active_group
        lines = ["## 你所在的组", f"- 组名：{group.name}（{group.group_id}）"]
        if group.summary:
            lines.append(f"- 组概述：{group.summary}")
        if role is not None:
            label = "组长" if role is GroupRole.LEADER else "组员"
            lines.append(f"- 你的角色：{label}")
        if duty:
            lines.append(f"- 你的职责：{duty}")

        shown = self.members[:MAX_MEMBER_CARDS]
        if shown:
            lines.append("- 组内成员（组长可直接用 task 委派）：")
            for card in shown:
                marker = "（你）" if card.agent_id == self_agent_id else ""
                lines.append(f"  {card.as_prompt_line()}{marker}")
        hidden = len(self.members) - len(shown)
        if hidden > 0:
            lines.append(f"  - ……其余 {hidden} 名成员请用 get_group_overview 查询")

        discover = self.discoverable_groups[:MAX_DISCOVERABLE_CARDS]
        if discover:
            lines.append("")
            lines.append("## 可协作的其他组（仅组长可见）")
            for item in discover:
                summary = item.summary or "无概述"
                lines.append(
                    f"- {item.name}（{item.group_id}）：{summary}；组长 {item.leader_agent_id}"
                )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class GroupMemberContext:
    """我的全部组籍：我在哪些组、分别是什么角色。"""

    memberships: tuple[GroupMembership, ...]
    active_group_id: str

    def __post_init__(self) -> None:
        if not self.memberships:
            raise ValueError("GroupMemberContext 至少需要一条组籍")
        if self.role_in(self.active_group_id) is None:
            raise ValueError(f"生效组不在组籍内: {self.active_group_id}")

    def role_in(self, group_id: str) -> GroupRole | None:
        for item in self.memberships:
            if item.group_id == group_id:
                return item.role
        return None

    def duty_in(self, group_id: str) -> str:
        for item in self.memberships:
            if item.group_id == group_id:
                return item.duty
        return ""

    @property
    def leader_group_ids(self) -> tuple[str, ...]:
        return tuple(
            item.group_id for item in self.memberships if item.role is GroupRole.LEADER
        )

    @property
    def member_group_ids(self) -> tuple[str, ...]:
        return tuple(
            item.group_id for item in self.memberships if item.role is GroupRole.MEMBER
        )

    @property
    def is_active_leader(self) -> bool:
        return self.role_in(self.active_group_id) is GroupRole.LEADER


# ============================================================
# 抽象基类与两个具体实现
# ============================================================


@dataclass(frozen=True, slots=True)
class HarnessAgentContextBase(ABC):
    """Agent 定义期上下文的抽象基类。

    组字段同时为空 ⇒ 普通 Agent；同时非空 ⇒ 团队 Agent。
    具体由 ``StandaloneAgentContext`` / ``TeamAgentContext`` 表达，
    业务代码不应直接实例化本类。
    """

    identity: AgentIdentity
    system_prompt: str
    model: ModelOverride = field(default_factory=ModelOverride)
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    group_context: GroupContext | None = None
    member_context: GroupMemberContext | None = None

    def __post_init__(self) -> None:
        self.validate()

    # ---------- 多态能力 ----------

    @property
    @abstractmethod
    def mode(self) -> AgentMode:
        ...

    @abstractmethod
    def allowed_team_tools(self) -> frozenset[str]:
        """该 Agent 允许看到的团队工具名集合。"""

    @abstractmethod
    def render_group_section(self) -> str:
        """注入系统提示词的组信息段；普通 Agent 返回空串。"""

    # ---------- 公共能力 ----------

    @property
    def is_team_agent(self) -> bool:
        return self.group_context is not None and self.member_context is not None

    @property
    def is_active_leader(self) -> bool:
        return bool(self.member_context and self.member_context.is_active_leader)

    def resolve_model(self, user_defaults: Mapping[str, Any]) -> dict[str, Any]:
        """模型三级继承：Agent → 组 → 用户默认。

        逐字段合并：某一级只配了 ``model_name`` 时，其余字段继续沿用下一级。
        """
        resolved = dict(user_defaults)
        group_model = self.group_context.model if self.group_context else None
        if group_model is not None and group_model.configured:
            resolved = group_model.merge(resolved)
        if self.model.configured:
            resolved = self.model.merge(resolved)
        return resolved

    def render(self) -> str:
        """角色提示词 + 组信息段。固定的 Harness 策略由装配层追加。"""
        sections = [self.system_prompt.strip(), self.render_group_section().strip()]
        return "\n\n".join(part for part in sections if part)

    def validate(self) -> None:
        if (self.group_context is None) != (self.member_context is None):
            raise ValueError("group_context 与 member_context 必须同时为空或同时非空")
        if self.group_context is None or self.member_context is None:
            return
        if self.group_context.active_group.group_id != self.member_context.active_group_id:
            raise ValueError("GroupContext 与 GroupMemberContext 的生效组不一致")


@dataclass(frozen=True, slots=True)
class StandaloneAgentContext(HarnessAgentContextBase):
    """普通 Agent：没有任何组相关上下文。"""

    @property
    def mode(self) -> AgentMode:
        return AgentMode.STANDALONE

    def allowed_team_tools(self) -> frozenset[str]:
        return frozenset()

    def render_group_section(self) -> str:
        return ""

    def validate(self) -> None:
        super().validate()
        if self.group_context is not None or self.member_context is not None:
            raise ValueError("普通 Agent 不应携带组上下文")


@dataclass(frozen=True, slots=True)
class TeamAgentContext(HarnessAgentContextBase):
    """团队 Agent：在生效组里以组长或组员身份工作。"""

    @property
    def mode(self) -> AgentMode:
        return AgentMode.TEAM

    def allowed_team_tools(self) -> frozenset[str]:
        common = {"list_my_groups", "get_group_overview"}
        if self.is_active_leader:
            return frozenset(
                common | {"discover_agent_groups", "delegate_to_group_leader"}
            )
        return frozenset(common)

    def render_group_section(self) -> str:
        assert self.group_context is not None and self.member_context is not None
        active = self.member_context.active_group_id
        return self.group_context.render(
            self_agent_id=self.identity.agent_id,
            role=self.member_context.role_in(active),
            duty=self.member_context.duty_in(active),
        )

    def validate(self) -> None:
        super().validate()
        group, member = self.group_context, self.member_context
        if group is None or member is None:
            raise ValueError("团队 Agent 必须同时提供 group_context 与 member_context")

        leader_id = group.active_group.leader_agent_id
        member_ids = {card.agent_id for card in group.members}
        if leader_id and leader_id not in member_ids:
            raise ValueError("组长不在成员列表中")

        role = member.role_in(member.active_group_id)
        if role is None:
            raise ValueError("生效组不在组籍中")
        if (self.identity.agent_id == leader_id) != (role is GroupRole.LEADER):
            raise ValueError("组长标记与组籍角色不一致")


# ============================================================
# 内部工具
# ============================================================


def _unique_tuple(values: Any) -> tuple[str, ...]:
    """去重保序的字符串元组；用于能力清单。"""
    result: list[str] = []
    for item in list(values or []):
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    return tuple(result)
