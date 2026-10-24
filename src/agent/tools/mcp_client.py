"""从 FastMCP / 标准 MCP Server 加载 LangChain 工具。

配置文件默认位于项目根目录 ``mcp_servers.yaml``，也可用
``HARNESS_MCP_CONFIG_PATH`` 指定。支持两种常用传输：

- ``stdio``：客户端以子进程方式启动 MCP Server；
- ``streamable_http``：连接已经启动的 HTTP MCP Server。

配置文件只应来自可信来源：``stdio`` 会执行 ``command`` 指定的程序。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable

import yaml

from agent.config import PROJECT_ROOT


logger = logging.getLogger(__name__)

MCP_CONFIG_ENV = "HARNESS_MCP_CONFIG_PATH"
DEFAULT_MCP_CONFIG_PATH = PROJECT_ROOT / "mcp_servers.yaml"
SUPPORTED_TRANSPORTS = {"stdio", "streamable_http", "sse"}


def mcp_config_path(path: str | Path | None = None) -> Path:
    """返回 MCP 配置文件路径；显式参数优先于环境变量。"""
    if path is not None:
        return Path(path)
    configured = os.getenv(MCP_CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_MCP_CONFIG_PATH


def _validate_server(name: str, config: Any) -> dict[str, Any]:
    """校验单个 MCP Server 的连接配置。"""
    if not isinstance(config, dict):
        raise ValueError(f"MCP Server 配置必须是对象: {name}")

    transport = str(config.get("transport", "")).strip().lower()
    if transport in {"streamable-http", "http"}:
        transport = "streamable_http"
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(
            f"MCP Server {name} 的 transport 无效: {transport!r}；"
            f"可选值: {sorted(SUPPORTED_TRANSPORTS)}"
        )

    validated = dict(config)
    validated["transport"] = transport

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

    with resolved.open(encoding="utf-8-sig") as config_file:
        raw = yaml.safe_load(config_file) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"MCP 配置必须是 YAML 对象: {resolved}")

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

    连接或工具发现失败会立即抛出 ``RuntimeError``，不会静默降级为
    “没有工具”，以免 Agent 在缺少业务能力时继续运行。
    """
    names = list(dict.fromkeys(str(name) for name in server_names if str(name)))
    if not names:
        return []

    config = load_mcp_config(config_path)
    servers = config["servers"]
    unknown = set(names) - set(servers)
    if unknown:
        raise ValueError(f"未定义的 MCP Server: {sorted(unknown)}")

    # 延迟导入：未使用 MCP 的项目不必在启动阶段加载适配器依赖。
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({name: servers[name] for name in names})
    tools: list[Any] = []
    for name in names:
        try:
            server_tools = await client.get_tools(server_name=name)
        except Exception as error:
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
