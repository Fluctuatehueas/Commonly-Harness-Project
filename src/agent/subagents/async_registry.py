"""集中定义可由 Agent Protocol 托管的异步子 Agent。

接入方只需要：
1. 在本文件用 ``register_async_subagent`` 注册一个或多个异步子 Agent；
2. 在 ``configs/`` 下放对应的 YAML 配置；
3. 可选地提供一个异步工具加载器（例如 MCP 客户端）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import AsyncSubAgent


# Agent Protocol 只有一个图入口，具体子 Agent 由 context 中的
# ``subagent_name`` 决定，避免每新增一个子 Agent 都修改 langgraph.json。
ASYNC_SUBAGENT_GRAPH_ID = "async_subagent"

AsyncToolLoader = Callable[[], Awaitable[list[Any]]]


async def _empty_tool_loader() -> list[Any]:
    """默认不提供额外工具；接入方可以替换为 MCP 或本地工具加载器。"""
    return []


@dataclass(frozen=True)
class AsyncSubagentRegistration:
    """描述一个可独立启动的异步子 Agent 的固定配置。"""

    name: str  # 对外逻辑名，也是 Artifact Protocol 的 subagent_name
    description: str  # 让主 Agent 知道这个子 Agent 能干什么
    config_path: Path  # YAML 配置路径
    tool_loader: AsyncToolLoader = _empty_tool_loader  # 异步加载额外工具
    launch_instruction: str = ""  # 注入主 Agent 的委派规则


# 异步子智能体注册表：name -> configuration。
# 默认注册表为空，接入方按业务需要注册自己的子 Agent。
ASYNC_SUBAGENTS: dict[str, AsyncSubagentRegistration] = {}


def register_async_subagent(registration: AsyncSubagentRegistration) -> None:
    """注册一个异步子 Agent；重复名称会被拒绝，避免运行期歧义。"""
    if registration.name in ASYNC_SUBAGENTS:
        raise ValueError(f"异步子 Agent 名称重复: {registration.name}")
    ASYNC_SUBAGENTS[registration.name] = registration


def get_async_subagent_registration(name: str) -> AsyncSubagentRegistration:
    """按名称返回注册信息，未知名称直接失败。"""
    try:
        return ASYNC_SUBAGENTS[name]
    except KeyError as error:
        raise ValueError(f"未注册的异步子 Agent: {name}") from error


def get_async_subagent_names() -> set[str]:
    """返回已注册的异步子 Agent 名称集合，供技能分配白名单使用。"""
    return set(ASYNC_SUBAGENTS)


def get_async_subagent_specs(protocol_url: str) -> list[AsyncSubAgent]:
    """返回主 Agent 注册远程任务工具所需的最小子 Agent 描述。"""
    return [
        {
            "name": registration.name,
            "description": registration.description,
            "graph_id": ASYNC_SUBAGENT_GRAPH_ID,
            "url": protocol_url,
        }
        for registration in ASYNC_SUBAGENTS.values()
    ]


def get_async_subagent_instructions() -> str:
    """把每个注册项的 launch_instruction 拼成主 Agent 可读的委派规则。"""
    instructions = "\n".join(
        f"- {registration.launch_instruction}"
        for registration in ASYNC_SUBAGENTS.values()
        if registration.launch_instruction
    )
    if not instructions:
        return ""
    return f"""
## 异步任务委派

{instructions}
""".strip()
