"""MCP 工具注册表、命名空间、代理工具与 LKG 缓存。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from agent.config import MCP_ROOT
from agent.mcp.types import McpError, ServerConfig, ToolMeta

logger = logging.getLogger(__name__)

# LKG 工具缓存与 MCP 配置放在同一个统一目录下：
# 源码模式为项目 mcp/，打包模式为 %LOCALAPPDATA%/CommonHarness/mcp/。
CACHE_ROOT = MCP_ROOT / "cache"
MAX_TOOL_NAME_LENGTH = 64


def _json_schema_type(schema: dict[str, Any]) -> Any:
    """把常见 JSON Schema 转成 Python 类型。"""
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        # 多数模型端点只要求字符串枚举；用 str 保持 schema 简单稳定。
        return str
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next(
            (item for item in schema_type if item != "null"), "string"
        )
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return list[_json_schema_type(item_schema)]  # type: ignore[valid-type]
        return list[Any]
    if schema_type == "object":
        return dict[str, Any]
    if schema_type == "string":
        return str
    return Any


def build_args_schema(
    input_schema: dict[str, Any],
    model_name: str,
) -> type[BaseModel]:
    """把 MCP 的 inputSchema 转为 LangChain 可用的 Pydantic 参数模型。"""
    properties = input_schema.get("properties")
    required = set(input_schema.get("required") or [])
    fields: dict[str, Any] = {}
    if isinstance(properties, dict):
        for name, raw_schema in properties.items():
            schema = raw_schema if isinstance(raw_schema, dict) else {}
            python_type = _json_schema_type(schema)
            description = str(schema.get("description", ""))
            default = schema.get("default")
            if name in required:
                fields[str(name)] = (
                    python_type,
                    Field(..., description=description),
                )
            else:
                fields[str(name)] = (
                    python_type,
                    Field(default, description=description),
                )
    safe_name = "".join(
        char if char.isalnum() or char == "_" else "_" for char in model_name
    )
    return create_model(  # type: ignore[call-overload]
        safe_name or "McpToolArgs",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


class MCPProxyTool(BaseTool):
    """指向注册表的稳定代理工具。

    图里固定持有该对象；真实调用由 CallRouter 从连接池取会话执行。
    """

    server_id: str
    remote_name: str

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("MCP 工具只支持异步调用")

    async def _arun(self, **kwargs: Any) -> Any:
        from agent.mcp.runtime import mcp_runtime

        return await mcp_runtime.call_tool(
            self.server_id,
            self.remote_name,
            kwargs,
        )


def _cache_path(server_id: str) -> Path:
    safe = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in server_id
    )
    return CACHE_ROOT / safe / "tools.json"


class ToolRegistry:
    """全局工具注册表，工具可见性由服务状态驱动。"""

    def __init__(self) -> None:
        self._metas: dict[str, list[ToolMeta]] = {}
        self._proxies: dict[str, MCPProxyTool] = {}
        self._by_server: dict[str, list[str]] = {}
        self._generation = 0
        self._conflicts: dict[str, str] = {}

    @property
    def generation(self) -> int:
        return self._generation

    def exposed_names(self) -> set[str]:
        return set(self._proxies)

    def tool_metas(self, server_id: str) -> list[ToolMeta]:
        return list(self._metas.get(server_id, []))

    def tool_count(self, server_id: str) -> int:
        return len(self._by_server.get(server_id, []))

    def has_tools(self, server_id: str) -> bool:
        return bool(self._by_server.get(server_id))

    def conflicts(self) -> dict[str, str]:
        return dict(self._conflicts)

    def tools_for_servers(self, server_ids: Iterable[str]) -> list[BaseTool]:
        result: list[BaseTool] = []
        for server_id in dict.fromkeys(server_ids):
            for name in self._by_server.get(server_id, []):
                proxy = self._proxies.get(name)
                if proxy is not None:
                    result.append(proxy)
        return result

    def register(
        self,
        config: ServerConfig,
        metas: list[ToolMeta],
        *,
        source: str = "live_discovery",
        reserved_names: set[str] | None = None,
    ) -> bool:
        """注册/替换一个服务的工具；返回工具名集合是否变化。"""
        reserved = reserved_names or set()
        before = self.exposed_names()
        new_proxies: dict[str, MCPProxyTool] = {}
        new_names: list[str] = []
        used = {name for name in before if name not in set(self._by_server.get(config.server_id, []))}
        conflicts: dict[str, str] = {}

        for meta in metas:
            exposed = config.exposed_name(meta.remote_name)
            if not meta.allowlisted or not config.tool_allowed(meta.remote_name):
                continue
            if len(exposed) > MAX_TOOL_NAME_LENGTH:
                conflicts[exposed] = (
                    f"工具名超过 {MAX_TOOL_NAME_LENGTH} 字符，已跳过: {exposed}"
                )
                continue
            if exposed in used or exposed in reserved:
                conflicts[exposed] = (
                    f"工具名冲突，已跳过: {exposed}（来源 {config.server_id}）"
                )
                continue
            existing = self._proxies.get(exposed)
            proxy = existing or MCPProxyTool(
                name=exposed,
                description="",
                args_schema=build_args_schema(meta.input_schema, exposed),
                server_id=config.server_id,
                remote_name=meta.remote_name,
                handle_tool_error=True,
            )
            proxy.description = f"[MCP server: {config.server_id}]\n{meta.description}".strip()
            proxy.args_schema = build_args_schema(meta.input_schema, exposed)
            new_proxies[exposed] = proxy
            new_names.append(exposed)
            used.add(exposed)

        # 删除该服务旧工具
        old_names = self._by_server.get(config.server_id, [])
        for old in old_names:
            if old not in new_proxies:
                self._proxies.pop(old, None)

        for name, proxy in new_proxies.items():
            self._proxies[name] = proxy
        self._by_server[config.server_id] = new_names
        stored = [
            ToolMeta(
                server_id=meta.server_id,
                remote_name=meta.remote_name,
                exposed_name=config.exposed_name(meta.remote_name),
                description=meta.description,
                input_schema=meta.input_schema,
                allowlisted=meta.allowlisted,
                source=source,
            )
            for meta in metas
            if config.exposed_name(meta.remote_name) in new_proxies
        ]
        self._metas[config.server_id] = stored
        self._conflicts.update(conflicts)
        if conflicts:
            logger.warning("MCP 工具注册冲突: %s", conflicts)

        after = self.exposed_names()
        changed = before != after
        if changed:
            self._generation += 1
        return changed

    def unregister(self, server_id: str) -> bool:
        before = self.exposed_names()
        names = self._by_server.pop(server_id, [])
        for name in names:
            self._proxies.pop(name, None)
        self._metas.pop(server_id, None)
        changed = before != self.exposed_names()
        if changed:
            self._generation += 1
        return changed

    def cached_metas(self, server_id: str) -> list[ToolMeta]:
        """读取 LKG 缓存；损坏时返回空。"""
        path = _cache_path(server_id)
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("读取 MCP 工具缓存失败: %s", path, exc_info=True)
            return []
        items = value.get("tools") if isinstance(value, dict) else value
        if not isinstance(items, list):
            return []
        return [
            ToolMeta.from_dict(item)
            for item in items
            if isinstance(item, dict)
        ]

    def save_cache(self, server_id: str, metas: list[ToolMeta]) -> None:
        """把最近一次成功发现的工具写入 LKG 缓存。"""
        path = _cache_path(server_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "server_id": server_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "tools": [
                    {**meta.to_dict(), "source": "lkg_cache"} for meta in metas
                ],
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("写入 MCP 工具缓存失败: %s", path, exc_info=True)

    def clear_cache(self, server_id: str) -> None:
        path = _cache_path(server_id)
        path.unlink(missing_ok=True)
