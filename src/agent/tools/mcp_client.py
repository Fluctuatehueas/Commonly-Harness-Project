"""从 FastMCP / 标准 MCP Server 加载 LangChain 工具。

配置文件默认使用统一的 ``mcp/servers.json``，也可用
``HARNESS_MCP_CONFIG_PATH`` 指定。支持两种常用传输：

- ``stdio``：客户端以子进程方式启动 MCP Server；
- ``streamable_http``：连接已经启动的 HTTP MCP Server。

配置文件只应来自可信来源：``stdio`` 会执行 ``command`` 指定的程序。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

import yaml

from agent.config import MCP_CONFIG_PATH


logger = logging.getLogger(__name__)

MCP_CONFIG_ENV = "HARNESS_MCP_CONFIG_PATH"
DEFAULT_MCP_CONFIG_PATH = MCP_CONFIG_PATH
SUPPORTED_TRANSPORTS = {"stdio", "streamable_http", "sse"}
# 只在本项目内使用、不传给 MCP 传输层的调度字段。
MCP_LOCAL_KEYS = {
    "description",
    "enabled",
    "optional",
    "preconnect",
    "criticality",
    "namespace",
    "allow_tools",
    "deny_tools",
    "connect_timeout_ms",
    "call_timeout_ms",
    "retry",
    "health_check",
    "pool",
    "tags",
}


def mcp_config_path(path: str | Path | None = None) -> Path:
    """返回 MCP 配置文件路径；显式参数优先于环境变量。"""
    if path is not None:
        return Path(path)
    configured = os.getenv(MCP_CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_MCP_CONFIG_PATH


def _validate_server(name: str, config: Any) -> dict[str, Any]:
    """校验单个 MCP Server 的连接配置。

    支持两个项目内调度字段：
    - ``enabled``：默认 ``true``；为 ``false`` 时不校验连接参数、也不会被加载。
    - ``optional``：默认 ``false``；为 ``true`` 时连接失败只告警并跳过，
      不阻断主 Agent 构图（用于尚未在本机安装的第三方视觉服务）。
    """
    if not isinstance(config, dict):
        raise ValueError(f"MCP Server 配置必须是对象: {name}")

    validated = dict(config)
    enabled = validated.get("enabled", True)
    optional = validated.get("optional", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"MCP Server {name} 的 enabled 必须是布尔值")
    if not isinstance(optional, bool):
        raise ValueError(f"MCP Server {name} 的 optional 必须是布尔值")
    validated["enabled"] = enabled
    validated["optional"] = optional

    transport = str(validated.get("transport", "")).strip().lower()
    if transport in {"streamable-http", "http"}:
        transport = "streamable_http"
    validated["transport"] = transport

    if not enabled:
        # 未启用的服务允许留空 command / url，方便先声明后安装。
        return validated

    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(
            f"MCP Server {name} 的 transport 无效: {transport!r}；"
            f"可选值: {sorted(SUPPORTED_TRANSPORTS)}"
        )

    if transport == "stdio":
        command = validated.get("command")
        args = validated.get("args", [])
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"stdio MCP Server {name} 缺少 command")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError(f"stdio MCP Server {name} 的 args 必须是字符串列表")
        env = validated.get("env")
        if env is not None and not isinstance(env, dict):
            raise ValueError(f"stdio MCP Server {name} 的 env 必须是对象")
        validated["args"] = args
    else:
        url = validated.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f"{transport} MCP Server {name} 缺少合法的 url")
        headers = validated.get("headers")
        if headers is not None and not isinstance(headers, dict):
            raise ValueError(f"MCP Server {name} 的 headers 必须是对象")

    return validated


def load_mcp_config(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """读取并校验 MCP 配置。

    Returns:
        ``{"main_servers": list[str], "servers": dict[str, dict]}``。
        默认配置文件不存在时返回空配置，便于未接入 MCP 的项目直接启动。
    """
    resolved = mcp_config_path(path)
    if not resolved.exists():
        if path is not None or os.getenv(MCP_CONFIG_ENV):
            raise FileNotFoundError(f"MCP 配置文件不存在: {resolved}")
        logger.info("未找到 MCP 配置文件，跳过 MCP 工具加载: %s", resolved)
        return {"main_servers": [], "servers": {}}

    text = resolved.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        # 兼容仍在使用 YAML 的旧配置或显式传入的 YAML 路径。
        raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"MCP 配置必须是 JSON 对象: {resolved}")

    raw_servers = raw.get("servers", {})
    if not isinstance(raw_servers, dict):
        raise ValueError(f"MCP 配置的 servers 必须是对象: {resolved}")
    servers = {
        str(name): _validate_server(str(name), config)
        for name, config in raw_servers.items()
    }

    raw_main = raw.get("main_servers", [])
    if not isinstance(raw_main, list) or not all(
        isinstance(name, str) and name for name in raw_main
    ):
        raise ValueError(f"MCP 配置的 main_servers 必须是字符串列表: {resolved}")
    unknown = set(raw_main) - set(servers)
    if unknown:
        raise ValueError(
            f"main_servers 引用了未定义的 MCP Server: {sorted(unknown)}"
        )

    return {"main_servers": list(dict.fromkeys(raw_main)), "servers": servers}


def _ensure_unique_tool_names(tools: list[Any]) -> None:
    """拒绝跨 MCP Server 的同名工具，避免模型调用时出现歧义。"""
    names = [str(getattr(tool, "name", "")) for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"MCP 工具名称重复: {duplicates}")


async def load_mcp_tools_for_servers(
    server_names: Iterable[str],
    *,
    config_path: str | Path | None = None,
) -> list[Any]:
    """加载指定 MCP Server 的全部工具。

    - ``enabled: false`` 的 Server 直接跳过（尚未安装 / 未启用的第三方服务）。
    - 连接或工具发现失败时，``optional: true`` 的 Server 只告警并跳过；
      其余 Server 立即抛出 ``RuntimeError``，不静默降级为“没有工具”。
    """
    names = list(dict.fromkeys(str(name) for name in server_names if str(name)))
    if not names:
        return []

    config = load_mcp_config(config_path)
    servers = config["servers"]
    unknown = set(names) - set(servers)
    if unknown:
        raise ValueError(f"未定义的 MCP Server: {sorted(unknown)}")

    selected: list[str] = []
    for name in names:
        if not servers[name].get("enabled", True):
            logger.info("MCP Server %s 未启用，跳过工具加载", name)
            continue
        selected.append(name)
    if not selected:
        return []

    # 延迟导入：未使用 MCP 的项目不必在启动阶段加载适配器依赖。
    from langchain_mcp_adapters.client import MultiServerMCPClient

    # description / enabled / optional 只用于本项目调度，不能传给传输层。
    connections = {
        name: {
            key: value
            for key, value in servers[name].items()
            if key not in MCP_LOCAL_KEYS
        }
        for name in selected
    }
    client = MultiServerMCPClient(connections)
    tools: list[Any] = []
    for name in selected:
        try:
            server_tools = await client.get_tools(server_name=name)
        except Exception as error:
            if servers[name].get("optional", False):
                logger.warning(
                    "可选 MCP Server %s 不可用，已跳过: %s", name, error
                )
                continue
            logger.exception("加载 MCP Server %s 的工具失败", name)
            raise RuntimeError(f"无法加载 MCP Server 的工具: {name}") from error
        logger.info("已从 MCP Server %s 加载 %d 个工具", name, len(server_tools))
        tools.extend(server_tools)

    _ensure_unique_tool_names(tools)
    return tools


async def load_main_mcp_tools(
    *,
    config_path: str | Path | None = None,
) -> list[Any]:
    """加载配置中 ``main_servers`` 指定的工具，供主 Agent 使用。"""
    config = load_mcp_config(config_path)
    return await load_mcp_tools_for_servers(
        config["main_servers"],
        config_path=config_path,
    )
