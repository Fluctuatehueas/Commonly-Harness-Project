"""AgentTeam：组、组籍与定义期上下文的装配。

对外只暴露稳定的上下文对象与两个 Store/Factory；运行期装配与工具在
``assembly`` / ``middleware`` / ``tools`` / ``runner`` 中，由主 Agent 构图时使用。
"""

from agent.agent_team.context import (
    AgentCapabilities,
    AgentCard,
    AgentIdentity,
    AgentMode,
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
from agent.agent_team.factory import (
    AgentContextFactory,
    GroupNotFoundError,
    NotGroupMemberError,
)
from agent.agent_team.store import AgentGroupStore

__all__ = [
    "AgentCapabilities",
    "AgentCard",
    "AgentContextFactory",
    "AgentGroupStore",
    "AgentIdentity",
    "AgentMode",
    "GroupContext",
    "GroupDescriptor",
    "GroupMemberContext",
    "GroupMembership",
    "GroupNotFoundError",
    "GroupRole",
    "GroupVisibility",
    "HarnessAgentContextBase",
    "ModelOverride",
    "NotGroupMemberError",
    "StandaloneAgentContext",
    "TeamAgentContext",
]
