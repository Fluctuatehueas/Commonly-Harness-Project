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
# 只读子 Agent 的工具级拒绝名单：转成 DeniedToolsMiddleware 后挂到子 Agent 栈。
# 不用 permissions：路径级权限拦不住 execute 这类 shell 工具。
DENY_TOOLS_KEY = "deny_tools"
# 允许声明"有则绑定、无则跳过"的工具：用于接入方后续才提供的可选工具，
# 避免尚未注册时整个主 Agent 构图失败。
OPTIONAL_TOOLS_KEY = "optional_tools"
# YAML 里可用的模型别名，避免把模型对象/凭据写进配置。
MODEL_ALIASES = {"main", "vision"}


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


def _validate_tool_names(value: Any, key: str, path: Path) -> list[str]:
    """校验 tools / optional_tools 是否为去重的非空字符串列表。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(name, str) and name for name in value
    ):
        raise ValueError(f"子 Agent 的 {key} 必须是字符串列表: {path}")
    if len(value) != len(set(value)):
        raise ValueError(f"子 Agent 的 {key} 存在重复项: {path}")
    return list(value)


def _resolve_model(value: Any) -> Any:
    """把 YAML 里的 ``main`` / ``vision`` 别名解析为模型实例。

    其他值原样返回，交给 deepagents / init_chat_model 处理。
    """
    if not isinstance(value, str) or value not in MODEL_ALIASES:
        return value
    # 延迟导入：只读配置解析不应拉起模型客户端。
    from agent.config import get_main_model, get_vision_model

    return get_vision_model() if value == "vision" else get_main_model()


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

    # 工具列表：tools 必须全部可用；optional_tools 只绑定当前存在的部分。
    configured_names = _validate_tool_names(config["tools"], "tools", path)
    optional_names = _validate_tool_names(
        config.get(OPTIONAL_TOOLS_KEY), OPTIONAL_TOOLS_KEY, path
    )
    overlap = set(configured_names) & set(optional_names)
    if overlap:
        raise ValueError(
            f"子 Agent 的 tools 与 optional_tools 重复: {sorted(overlap)}"
        )

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

    bound_tools = [tool_map[name] for name in configured_names]
    bound_tools.extend(
        tool_map[name] for name in optional_names if name in tool_map
    )

    subagent = {
        "name": config["name"],
        "description": config["description"],
        "system_prompt": config["system_prompt"],
        "tools": bound_tools,
    }
    for key in OPTIONAL_SUBAGENT_KEYS:
        if key in config and config[key] is not None:
            subagent[key] = _resolve_model(config[key]) if key == "model" else config[key]

    denied = _validate_tool_names(config.get(DENY_TOOLS_KEY), DENY_TOOLS_KEY, path)
    if denied:
        # 延迟导入：只解析配置的调用方不应拉起完整中间件栈。
        from agent.middlewares.tool_guard import DeniedToolsMiddleware

        subagent["middleware"] = [
            *(subagent.get("middleware") or []),
            DeniedToolsMiddleware(denied),
        ]
    return subagent


async def aload_subagent(
    config_path: str | Path,
    available_tools: Iterable[Any] = (),
    *,
    local_tools: Iterable[Any] = (),
    mcp_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """读取 YAML，加载其 ``mcp_servers`` 声明的工具，再构建子 Agent 配置。

    MCP 工具名无法在 YAML 里预先声明，因此语义是"声明服务即绑定其全部工具"：
    ``load_subagent`` 只绑定点名的 ``tools`` / ``optional_tools``，这里再把
    MCP 工具追加进去，否则 ``mcp_servers`` 会加载成功但一个工具也绑不上。
    """
    path = Path(config_path)
    config = read_subagent_config(path)
    mcp_servers = _validate_mcp_servers(config, path)

    tools = list(available_tools)
    mcp_tools: list[Any] = []
    if mcp_servers:
        if mcp_config_path is not None:
            # 独立运行（如异步子 Agent）且显式指定另一份配置时，不能复用
            # 主进程的运行时与连接池，保持即时加载语义。
            from agent.tools.mcp_client import load_mcp_tools_for_servers

            mcp_tools = await load_mcp_tools_for_servers(
                mcp_servers,
                config_path=mcp_config_path,
            )
        else:
            # 与主 Agent 共用同一个 MCP 运行时与连接池；
            # 只对子 Agent 声明的服务做 scope 级懒发现，不连接其他配置。
            from agent.mcp.runtime import mcp_runtime

            mcp_tools = await mcp_runtime.tools_for_servers(mcp_servers)
        tools.extend(mcp_tools)

    subagent = load_subagent(path, tools, local_tools=local_tools)

    bound_names = {str(getattr(tool, "name", "")) for tool in subagent["tools"]}
    subagent["tools"].extend(
        tool
        for tool in mcp_tools
        if str(getattr(tool, "name", "")) not in bound_names
    )
    return subagent
