"""MCP 工具调用路由：状态门控、白名单、超时、取消与错误归一化。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

import mcp.types as mcp_types

from agent.mcp.client_registry import MCPClientRegistry, PooledSession
from agent.mcp.observer import McpObserver
from agent.mcp.registry import ToolRegistry
from agent.mcp.types import (
    ErrorCategory,
    McpError,
    ServerConfig,
    ServerState,
)

logger = logging.getLogger(__name__)

ConfigProvider = Callable[[str], ServerConfig | None]
StateProvider = Callable[[str], ServerState]
FailureHandler = Callable[[str, McpError], Any]


def classify_exception(error: BaseException, *, server_id: str = "") -> McpError:
    """按异常类型与文本做最小可用的错误分类。"""
    if isinstance(error, McpError):
        return error
    if isinstance(error, asyncio.TimeoutError):
        return McpError(
            "MCP 操作超时",
            code="MCP_TIMEOUT",
            category=ErrorCategory.TIMEOUT,
            server_id=server_id,
            retryable=True,
            http_status=504,
        )
    text = f"{type(error).__name__}: {error}".lower()
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "auth")):
        return McpError(
            "MCP 服务需要鉴权或凭据已失效",
            code="MCP_AUTH_REQUIRED",
            category=ErrorCategory.AUTH,
            server_id=server_id,
            retryable=False,
            http_status=401,
        )
    if any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection",
            "connect",
            "503",
            "502",
            "504",
            "429",
            "rate limit",
            "temporarily",
            "process exited",
            "broken pipe",
        )
    ):
        return McpError(
            "MCP 服务暂时不可用",
            code="MCP_CONNECT_FAILED",
            category=ErrorCategory.TRANSIENT,
            server_id=server_id,
            retryable=True,
            http_status=502,
        )
    if any(token in text for token in ("config", "invalid", "missing", "schema")):
        return McpError(
            "MCP 配置无效",
            code="MCP_CONFIG_INVALID",
            category=ErrorCategory.CONFIG,
            server_id=server_id,
            retryable=False,
            http_status=422,
        )
    return McpError(
        f"MCP 调用失败: {error}",
        code="MCP_CALL_FAILED",
        category=ErrorCategory.PERMANENT,
        server_id=server_id,
        retryable=False,
        http_status=502,
    )


def _content_block_text(block: Any) -> str:
    """把 MCP 内容块转成可读文本。"""
    if isinstance(block, mcp_types.TextContent):
        return block.text
    if isinstance(block, mcp_types.ImageContent):
        return f"[image: {block.mimeType}, {len(block.data)} bytes]"
    if isinstance(block, mcp_types.AudioContent):
        return f"[audio: {block.mimeType}, {len(block.data)} bytes]"
    if isinstance(block, mcp_types.EmbeddedResource):
        resource = block.resource
        text = getattr(resource, "text", None)
        if text is not None:
            return str(text)
        return f"[resource: {getattr(resource, 'uri', 'unknown')}]"
    if isinstance(block, mcp_types.ResourceLink):
        return f"[resource] {block.name}: {block.uri}"
    return str(block)


class CallRouter:
    """把代理工具调用路由到连接池中的 MCP 会话。"""

    def __init__(
        self,
        pool: MCPClientRegistry,
        registry: ToolRegistry,
        observer: McpObserver,
        *,
        config_provider: ConfigProvider,
        state_provider: StateProvider,
        failure_handler: FailureHandler | None = None,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._observer = observer
        self._config_provider = config_provider
        self._state_provider = state_provider
        self._failure_handler = failure_handler

    async def call(
        self,
        server_id: str,
        remote_name: str,
        args: dict[str, Any],
    ) -> str:
        request_id = f"mcp-{uuid.uuid4().hex[:12]}"
        config = self._config_provider(server_id)
        if config is None:
            return McpError(
                f"MCP 服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                tool_name=remote_name,
            ).to_json()

        state = self._state_provider(server_id)
        if state in {
            ServerState.DISABLED,
            ServerState.FAILED,
            ServerState.NEEDS_AUTH,
            ServerState.PENDING,
            ServerState.STOPPING,
        }:
            error = McpError(
                f"MCP 服务当前不可用（{state.value}）",
                code="MCP_UNAVAILABLE",
                category=ErrorCategory.UNAVAILABLE,
                server_id=server_id,
                tool_name=remote_name,
                state=state,
                http_status=503,
            )
            self._observer.record(
                "mcp.call",
                server_id=server_id,
                level="warning",
                tool=remote_name,
                request_id=request_id,
                outcome="unavailable",
                state=state.value,
            )
            return error.to_json()

        if not config.tool_allowed(remote_name):
            error = McpError(
                f"MCP 工具被白名单拦截: {remote_name}",
                code="MCP_TOOL_BLOCKED",
                category=ErrorCategory.PERMANENT,
                server_id=server_id,
                tool_name=remote_name,
                state=state,
                http_status=403,
            )
            self._observer.record(
                "mcp.tool_blocked",
                server_id=server_id,
                level="warning",
                tool=remote_name,
                request_id=request_id,
            )
            return error.to_json()

        started = time.monotonic()
        entry: PooledSession | None = None
        pool_reused = False
        try:
            entry = await self._pool.acquire(config)
            pool_reused = entry.reused
            result = await self._pool.call_tool(
                entry,
                remote_name,
                args,
                timeout_ms=config.call_timeout_ms,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            text = self._format_result(result)
            if result.isError:
                error = McpError(
                    text or "MCP 工具执行返回错误",
                    code="MCP_TOOL_ERROR",
                    category=ErrorCategory.TOOL,
                    server_id=server_id,
                    tool_name=remote_name,
                    retryable=False,
                    state=state,
                    http_status=422,
                )
                self._observer.record(
                    "mcp.call",
                    server_id=server_id,
                    level="warning",
                    tool=remote_name,
                    request_id=request_id,
                    duration_ms=duration_ms,
                    outcome="tool_error",
                    error_category=ErrorCategory.TOOL.value,
                    pool_reused=pool_reused,
                )
                return error.to_json()

            self._observer.record(
                "mcp.call",
                server_id=server_id,
                tool=remote_name,
                request_id=request_id,
                duration_ms=duration_ms,
                outcome="ok",
                pool_reused=pool_reused,
            )
            return text
        except McpError as error:
            self._observer.record(
                "mcp.call",
                server_id=server_id,
                level="error",
                tool=remote_name,
                request_id=request_id,
                outcome="error",
                error_category=error.category.value,
                error_code=error.code,
            )
            await self._notify_failure(server_id, error)
            return error.to_json()
        except Exception as exc:
            error = classify_exception(exc, server_id=server_id)
            error.tool_name = remote_name
            self._observer.record(
                "mcp.call",
                server_id=server_id,
                level="error",
                tool=remote_name,
                request_id=request_id,
                outcome="error",
                error_category=error.category.value,
                error_code=error.code,
                error=str(exc),
            )
            await self._notify_failure(server_id, error)
            return error.to_json()
        finally:
            if entry is not None:
                await self._pool.release(entry)

    async def _notify_failure(self, server_id: str, error: McpError) -> None:
        if self._failure_handler is None:
            return
        result = self._failure_handler(server_id, error)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    def _format_result(result: mcp_types.CallToolResult) -> str:
        parts = [
            _content_block_text(block) for block in (result.content or [])
        ]
        # 只有没有可读文本时才回退到 structuredContent，避免同一结果重复两遍。
        if result.structuredContent is not None and not any(parts):
            parts.append(
                json.dumps(
                    result.structuredContent,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return "\n\n".join(part for part in parts if part).strip()
