"""通用的同步子 Agent 配置加载器。

``load_subagent`` 用于已经拿到全部工具的同步场景；
``aload_subagent`` 额外读取 YAML 中的 ``mcp_servers`` 并加载 MCP 工具，
适合主 Agent 构图或异步子 Agent 的工具加载器。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

# 可以查询的配置关键字
REQUIRED_CONFIG_KEYS = {"name", "description", "system_prompt", "tools"}
# 子智能体可操作的关键字
OPTIONAL_SUBAGENT_KEYS = {"model", "skills", "middleware", "interrupt_on", "permissions"}
# 由 aload_subagent 读取、不会透传给 create_deep_agent 的 MCP 声明
MCP_SERVERS_KEY = "mcp_servers"


def read_subagent_config(config_path: str | Path) -> dict[str, Any]:
    """读取 YAML 配置并校验顶层结构。"""
    path = Path(config_path)
    # utf-8-sig：兼容 Windows 编辑器可能写出的 BOM。
    with path.open(encoding="utf-8-sig") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict) or not REQUIRED_CONFIG_KEYS.issubset(config):
        raise ValueError(f"子 Agent 配置无效: {path}")
    return config


def _validate_mcp_servers(config: dict[str, Any], path: Path) -> list[str]:
    """校验 YAML 中的 mcp_servers 声明。"""
    value = config.get(MCP_SERVERS_KEY, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(name, str) and name for name in value
    ):
        raise ValueError(f"子 Agent 的 mcp_servers 必须是字符串列表: {path}")
    if len(value) != len(set(value)):
        raise ValueError(f"子 Agent 的 mcp_servers 存在重复项: {path}")
    return list(value)


def load_subagent(
    config_path: str | Path,
    available_tools: Iterable[Any],
    *,
    local_tools: Iterable[Any] = (),
) -> dict[str, Any]:
    """同步读取一个 YAML 子 Agent 配置，并绑定配置中声明的工具。

    Args:
        config_path: 子 Agent YAML 配置路径。
        available_tools: 当前运行时可提供的 MCP 或其他工具。
        local_tools: 不来自 MCP、但允许由配置声明的本地工具。

    Returns:
        可直接传给 ``create_deep_agent(subagents=...)`` 的配置字典。

    Raises:
        ValueError: 配置结构无效、工具名称重复或声明的工具不可用。
    """
    path = Path(config_path)
    config = read_subagent_config(path)
    _validate_mcp_servers(config, path)

    # 工具列表
    configured_names = config["tools"]
    if not isinstance(configured_names, list) or not all(
        isinstance(name, str) and name for name in configured_names
    ):
        raise ValueError(f"子 Agent 工具配置无效: {path}")
    if len(configured_names) != len(set(configured_names)):
        raise ValueError(f"子 Agent 工具配置重复: {path}")

    tool_map: dict[str, Any] = {}
    for tool in [*available_tools, *local_tools]:
        tool_name = str(getattr(tool, "name", ""))
        if not tool_name:
            continue
        if tool_name in tool_map:
            raise ValueError(f"子 Agent 工具名称重复: {tool_name}")
        tool_map[tool_name] = tool

    missing_tools = set(configured_names) - set(tool_map)
    if missing_tools:
        names = ", ".join(sorted(missing_tools))
        raise ValueError(f"子 Agent 缺少配置工具: {names}")

    subagent = {
        "name": config["name"],
        "description": config["description"],
        "system_prompt": config["system_prompt"],
        "tools": [tool_map[name] for name in configured_names],
    }
    for key in OPTIONAL_SUBAGENT_KEYS:
        if key in config and config[key] is not None:
            subagent[key] = config[key]
    return subagent


async def aload_subagent(
    config_path: str | Path,
    available_tools: Iterable[Any] = (),
    *,
    local_tools: Iterable[Any] = (),
    mcp_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """读取 YAML，加载其 ``mcp_servers`` 声明的工具，再构建子 Agent 配置。"""
    path = Path(config_path)
    config = read_subagent_config(path)
    mcp_servers = _validate_mcp_servers(config, path)

    tools = list(available_tools)
    if mcp_servers:
        # 延迟导入：不使用 MCP 的子 Agent 不必加载适配器依赖。
        from agent.tools.mcp_client import load_mcp_tools_for_servers

        tools.extend(
            await load_mcp_tools_for_servers(
                mcp_servers,
                config_path=mcp_config_path,
            )
        )
    return load_subagent(path, tools, local_tools=local_tools)
