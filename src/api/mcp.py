"""MCP 服务管理 REST API。

注意：`/mcp` 是 Harness 的管理接口，不是 MCP Server 端点。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.config import MCP_ROOT, mcp_npm_cache_dir, mcp_workspace_root
from agent.mcp.config_store import (
    extract_vendor_servers,
    normalize_vendor_entry,
)
from agent.mcp.runtime import mcp_runtime
from agent.mcp.types import McpError
from utils.runtime_probe import (
    probe_command,
    probe_npm_cache,
    required_commands_for_stdio,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class ServerUpsert(BaseModel):
    """新增/更新 MCP 服务配置。"""

    server_id: str = Field(..., min_length=1, max_length=64)
    description: str = Field("", max_length=500)
    transport: Literal[
        "stdio", "streamable_http", "sse", "http", "streamable-http"
    ] = "stdio"
    command: str | None = Field(None, max_length=1000)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(None, max_length=1000)
    url: str | None = Field(None, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    preconnect: bool = False
    # 自定义 MCP：用户自有后端项目，作为集成模式的业务能力入口。
    integration: bool = False
    criticality: Literal["required", "optional"] = "optional"
    namespace: str | None = Field(None, max_length=32)
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    connect_timeout_ms: int = Field(15000, ge=1000, le=120000)
    call_timeout_ms: int = Field(120000, ge=1000, le=300000)
    retry: dict[str, Any] | None = None
    health_check: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        transport = self.transport.replace("-", "_")
        if transport == "http":
            transport = "streamable_http"
        payload: dict[str, Any] = {
            "description": self.description,
            "transport": transport,
            "enabled": self.enabled,
            "preconnect": self.preconnect,
            "integration": self.integration,
            "criticality": self.criticality,
            "namespace": self.namespace or self.server_id,
            "allow_tools": self.allow_tools,
            "deny_tools": self.deny_tools,
            "connect_timeout_ms": self.connect_timeout_ms,
            "call_timeout_ms": self.call_timeout_ms,
            "tags": self.tags,
        }
        if transport == "stdio":
            payload["command"] = self.command or ""
            payload["args"] = self.args
            if self.env:
                payload["env"] = self.env
            if self.cwd:
                payload["cwd"] = self.cwd
        else:
            payload["url"] = self.url or ""
            if self.headers:
                payload["headers"] = self.headers
        if self.retry is not None:
            payload["retry"] = self.retry
        if self.health_check is not None:
            payload["health_check"] = self.health_check
        return payload


class EnableRequest(BaseModel):
    enabled: bool
    discover: bool = False


class ToolTestRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class VendorImportRequest(BaseModel):
    """粘贴 MCP 厂商提供的 JSON 配置。"""

    json_text: str = Field("", max_length=200_000)
    json_data: dict[str, Any] | None = None
    enabled: bool = True
    # 本次导入的服务是否属于用户自有后端项目（集成模式）。
    integration: bool = False


_AUTH_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "x-auth-token",
    "token",
}


def _parse_vendor_payload(
    body: VendorImportRequest,
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    """解析厂商 JSON，返回 (可导入条目, 解析失败条目)。"""
    if body.json_data is not None:
        payload: Any = body.json_data
    else:
        text = body.json_text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="请粘贴 MCP 配置 JSON")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=422, detail=f"JSON 格式不合法: {error.msg}（第 {error.lineno} 行）"
            ) from error

    try:
        raw_entries = extract_vendor_servers(payload)
    except McpError as error:
        raise _http_error(error) from error

    normalized: list[tuple[str, dict[str, Any]]] = []
    failed: list[dict[str, Any]] = []
    for server_id, raw in raw_entries:
        try:
            item = normalize_vendor_entry(server_id, raw)
        except McpError as error:
            failed.append({"server_id": server_id, "error": error.message})
            continue
        if not body.enabled:
            item["enabled"] = False
        if body.integration:
            item["integration"] = True
        normalized.append((server_id, item))
    return normalized, failed


def _has_auth_header(headers: dict[str, str] | None) -> bool:
    for key, value in (headers or {}).items():
        if key.strip().lower() in _AUTH_HEADER_KEYS:
            return True
        if isinstance(value, str) and (
            "bearer " in value.lower() or "${" in value
        ):
            return True
    return False


def _preflight_entry(server_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """检查单个 stdio 服务的本地运行前置条件。"""
    try:
        item = normalize_vendor_entry(server_id, raw)
    except McpError as error:
        return {
            "server_id": server_id,
            "transport": "",
            "ready": False,
            "error": error.message,
            "checks": [],
            "hint": "",
        }

    if item["transport"] != "stdio":
        headers = item.get("headers") or {}
        needs_key = not _has_auth_header(headers)
        hints: list[str] = []
        if needs_key:
            hints.append(
                "这是网络传输的 MCP：通常需要在 JSON 的 headers 中配置 API Key"
                "（如 Authorization: Bearer ${API_KEY}）。"
            )
        if item["transport"] == "sse":
            hints.append(
                "URL 以 /sse 结尾，将按 SSE 传输连接。"
            )
        hint = "".join(hints)
        return {
            "server_id": server_id,
            "transport": "network",
            "ready": True,
            "requires_api_key": needs_key,
            "checks": [],
            "hint": hint,
        }

    command = str(item.get("command") or "")
    args = item.get("args") or []
    required = required_commands_for_stdio(command, args) or [command]
    checks = [probe_command(name) for name in required]
    missing = [check for check in checks if not check["available"]]
    unsatisfied = [check for check in checks if check["available"] and not check["satisfied"]]
    hints = [
        f"{check['label']}：{check['hint']}（下载：{check['download']}）"
        for check in [*missing, *unsatisfied]
        if check.get("hint") or check.get("download")
    ]
    # npx / npm 型服务会下载包到 npm 缓存；运行时统一改写为项目内目录，
    # 这里提前告诉用户缓存落在哪，避免“系统缓存不可写”造成的困惑。
    cache_dir = (
        str(mcp_npm_cache_dir(server_id))
        if Path(command).stem.lower() in {"npx", "npm"}
        else ""
    )
    return {
        "server_id": server_id,
        "transport": "stdio",
        "command": command,
        "ready": not missing and not unsatisfied,
        "requires_api_key": False,
        "checks": checks,
        "hint": "；".join(hints),
        "cache_dir": cache_dir,
    }


def _http_error(error: McpError) -> HTTPException:
    return HTTPException(
        status_code=error.http_status,
        detail=error.to_dict()["error"],
    )


def _not_found(server_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "MCP_SERVER_NOT_FOUND",
            "message": f"MCP 服务不存在: {server_id}",
            "server_id": server_id,
        },
    )


@router.get("/runtime")
async def runtime_info() -> dict[str, Any]:
    """返回 MCP 运行时概览，不触发连接。"""
    return {
        "initialized": mcp_runtime.initialized,
        "config_error": mcp_runtime.config_error,
        "pool": mcp_runtime.pool.stats(),
        "server_count": len(mcp_runtime.server_ids()),
        "main_servers": mcp_runtime.main_server_ids(),
    }


@router.get("/config")
async def config_info() -> dict[str, Any]:
    """返回 MCP 统一配置文件与目录位置。"""
    return {
        "format": "json",
        "config_path": str(mcp_runtime.config_store.path),
        "root": str(MCP_ROOT),
        "config_error": mcp_runtime.config_error,
    }


@router.post("/preflight")
async def preflight_config(body: VendorImportRequest) -> dict[str, Any]:
    """在保存前检查 JSON 是否可用：stdio 依赖、网络 MCP 的 API Key 提醒。"""
    if body.json_data is not None:
        payload: Any = body.json_data
    else:
        text = body.json_text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="请粘贴 MCP 配置 JSON")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=422,
                detail=f"JSON 格式不合法: {error.msg}（第 {error.lineno} 行）",
            ) from error

    try:
        raw_entries = extract_vendor_servers(payload)
    except McpError as error:
        raise _http_error(error) from error

    results = [
        _preflight_entry(server_id, raw)
        for server_id, raw in raw_entries
    ]
    api_key_required = [
        item["server_id"] for item in results if item.get("requires_api_key")
    ]
    # 只在存在 npx / npm 型 stdio 服务时才探测系统 npm 缓存，避免多余进程。
    npm_cache = None
    if any(item.get("cache_dir") for item in results):
        npm_cache = {
            **probe_npm_cache(),
            "workspace_root": str(mcp_workspace_root()),
        }
    return {
        "servers": results,
        "invalid": [item for item in results if item.get("error")],
        "ready": all(item.get("ready") for item in results) if results else False,
        "requires_api_key": api_key_required,
        "npm_cache": npm_cache,
    }


@router.post("/import")
async def import_config(body: VendorImportRequest) -> dict[str, Any]:
    """粘贴厂商 JSON 后直接保存：工具/提示词/资源由服务自己上报。"""
    entries, failed = _parse_vendor_payload(body)
    if not entries:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MCP_CONFIG_EMPTY",
                "message": "没有可导入的 MCP 服务配置",
                "failed": failed,
            },
        )
    try:
        details = await mcp_runtime.import_servers(entries)
    except McpError as error:
        raise _http_error(error) from error
    return {
        "ok": True,
        "imported": [server_id for server_id, _ in entries],
        "failed": failed,
        "servers": details,
        "config_path": str(mcp_runtime.config_store.path),
    }


@router.get("/integrations")
async def list_integrations() -> dict[str, Any]:
    """返回用户自有后端项目的 MCP 列表；不会触发任何连接。

    聊天侧栏的【集成的项目】只依赖本接口，连接状态按运行态返回，
    用户需要先用 probe 建立连接后才能在该后端下操作会话。
    """
    items = [
        item for item in mcp_runtime.details() if bool(item.get("integration"))
    ]
    return {"items": items, "total": len(items)}


@router.get("/servers")
async def list_servers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=200),
    state: str = Query("", max_length=32),
    enabled: bool | None = Query(None),
    integration: bool | None = Query(None),
) -> dict[str, Any]:
    """分页返回服务状态；该接口不会建立任何 MCP 连接。"""
    items = mcp_runtime.details()
    needle = keyword.strip().casefold()
    if needle:
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    item["server_id"],
                    item["description"],
                    item["transport"],
                    item["namespace"],
                ]
            ).casefold()
        ]
    if state:
        items = [item for item in items if item["state"].casefold() == state.casefold()]
    if enabled is not None:
        items = [item for item in items if item["enabled"] is enabled]
    if integration is not None:
        items = [
            item for item in items if bool(item.get("integration")) is integration
        ]

    total = len(items)
    pages = max((total + page_size - 1) // page_size, 1)
    current = min(page, pages)
    start = (current - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": current,
        "page_size": page_size,
        "pages": pages,
        "config_error": mcp_runtime.config_error,
        "pool": mcp_runtime.pool.stats(),
    }


@router.get("/servers/{server_id}")
async def get_server(server_id: str) -> dict[str, Any]:
    detail = mcp_runtime.detail(server_id)
    if detail is None:
        raise _not_found(server_id)
    return detail


@router.post("/servers")
async def create_server(body: ServerUpsert) -> dict[str, Any]:
    if mcp_runtime.config_for(body.server_id) is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MCP_SERVER_EXISTS",
                "message": f"MCP 服务已存在: {body.server_id}",
                "server_id": body.server_id,
            },
        )
    try:
        return await mcp_runtime.upsert_server(body.server_id, body.to_payload())
    except McpError as error:
        raise _http_error(error) from error


@router.put("/servers/{server_id}")
async def update_server(server_id: str, body: ServerUpsert) -> dict[str, Any]:
    if body.server_id != server_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "MCP_CONFIG_INVALID", "message": "路径与请求体 server_id 不一致"},
        )
    try:
        return await mcp_runtime.upsert_server(server_id, body.to_payload())
    except McpError as error:
        raise _http_error(error) from error


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str) -> dict[str, bool]:
    try:
        await mcp_runtime.delete_server(server_id)
    except McpError as error:
        raise _http_error(error) from error
    return {"ok": True}


@router.post("/servers/{server_id}/enable")
async def enable_server(server_id: str, body: EnableRequest) -> dict[str, Any]:
    try:
        detail = await mcp_runtime.enable(server_id, body.enabled)
        if body.discover and body.enabled:
            detail = (await mcp_runtime.probe(server_id))["server"]
        return detail
    except McpError as error:
        raise _http_error(error) from error


@router.post("/servers/{server_id}/probe")
async def probe_server(server_id: str) -> dict[str, Any]:
    try:
        return await mcp_runtime.probe(server_id)
    except McpError as error:
        raise _http_error(error) from error


@router.post("/servers/{server_id}/restart")
async def restart_server(server_id: str) -> dict[str, Any]:
    try:
        return await mcp_runtime.restart(server_id)
    except McpError as error:
        raise _http_error(error) from error


@router.get("/servers/{server_id}/tools")
async def list_tools(
    server_id: str,
    discover: bool = Query(False),
) -> dict[str, Any]:
    """默认读 LKG；`discover=true` 时才触发一次懒连接发现。"""
    config = mcp_runtime.config_for(server_id)
    if config is None:
        raise _not_found(server_id)
    if discover:
        try:
            await mcp_runtime.probe(server_id)
        except McpError as error:
            raise _http_error(error) from error
    metas = mcp_runtime.registry.tool_metas(server_id)
    if not metas and not discover:
        metas = mcp_runtime.registry.cached_metas(server_id)
    return {
        "server_id": server_id,
        "state": mcp_runtime.state_for(server_id).value,
        "tools": [meta.to_dict() for meta in metas],
        "conflicts": mcp_runtime.registry.conflicts(),
    }


@router.post("/servers/{server_id}/tools/{tool_name}/test")
async def test_tool(
    server_id: str,
    tool_name: str,
    body: ToolTestRequest,
) -> dict[str, Any]:
    """测试调用；首次调用按需懒连接。"""
    config = mcp_runtime.config_for(server_id)
    if config is None:
        raise _not_found(server_id)
    if not config.tool_allowed(tool_name):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "MCP_TOOL_BLOCKED",
                "message": f"工具被白名单拦截: {tool_name}",
                "server_id": server_id,
                "tool": tool_name,
            },
        )
    text = await mcp_runtime.call_tool(server_id, tool_name, body.arguments)
    return {
        "server_id": server_id,
        "tool": tool_name,
        "state": mcp_runtime.state_for(server_id).value,
        "text": text,
    }


@router.get("/servers/{server_id}/logs")
async def server_logs(
    server_id: str,
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    if mcp_runtime.config_for(server_id) is None:
        raise _not_found(server_id)
    return {
        "server_id": server_id,
        "logs": mcp_runtime.logs(server_id, limit),
    }
