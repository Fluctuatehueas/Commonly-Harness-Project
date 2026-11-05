"""MCP 编排层的公共数据模型。

本模块只描述「Harness 作为 MCP Host / Client」所需的配置与运行状态，
不包含任何 MCP Server 侧实现。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Transport(str, Enum):
    """支持的 MCP 传输方式。"""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class ServerState(str, Enum):
    """单个 MCP Server 的生命周期状态。"""

    DORMANT = "Dormant"
    DISABLED = "Disabled"
    STARTING = "Starting"
    CONNECTED = "Connected"
    DEGRADED = "Degraded"
    PENDING = "Pending"
    NEEDS_AUTH = "NeedsAuth"
    FAILED = "Failed"
    STOPPING = "Stopping"


class ErrorCategory(str, Enum):
    """错误分类，决定是否重试以及进入哪个状态。"""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AUTH = "auth"
    CONFIG = "config"
    TOOL = "tool"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CIRCUIT_OPEN = "circuit_open"
    UNAVAILABLE = "unavailable"


class McpError(Exception):
    """带分类与结构化字段的 MCP 错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "MCP_ERROR",
        category: ErrorCategory = ErrorCategory.PERMANENT,
        server_id: str = "",
        tool_name: str = "",
        retryable: bool = False,
        state: ServerState | None = None,
        http_status: int = 502,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.category = category
        self.server_id = server_id
        self.tool_name = tool_name
        self.retryable = retryable
        self.state = state
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "server": self.server_id,
                "tool": self.tool_name,
                "category": self.category.value,
                "retryable": self.retryable,
                "state": self.state.value if self.state else None,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class RetryPolicy:
    """连接/健康检查的重试策略。"""

    max_attempts: int = 4
    base_delay_ms: int = 1000
    max_delay_ms: int = 30000
    jitter: float = 0.2
    retry_on: tuple[str, ...] = (
        ErrorCategory.TRANSIENT.value,
        ErrorCategory.TIMEOUT.value,
    )

    def delay_ms(self, attempt: int) -> int:
        """按指数退避计算下一次延迟（含抖动）。"""
        import random

        base = min(
            self.max_delay_ms,
            self.base_delay_ms * (2 ** max(attempt - 1, 0)),
        )
        spread = base * self.jitter
        return max(0, int(base + random.uniform(-spread, spread)))


@dataclass
class HealthPolicy:
    """健康检查策略；只对已连接实例生效。"""

    interval_ms: int = 30000
    timeout_ms: int = 5000
    failure_threshold: int = 3
    success_threshold: int = 1
    degraded_latency_ms: int = 0


@dataclass
class PoolPolicy:
    """连接池策略。"""

    idle_ttl_ms: int = 300000
    max_pool_size: int = 32
    connect_singleflight: bool = True
    max_concurrent_per_key: int = 4


@dataclass
class NamespacePolicy:
    """工具命名空间策略。"""

    mode: str = "prefix"  # legacy | prefix
    separator: str = "__"


LOCAL_CONFIG_KEYS = {
    "description",
    "enabled",
    "integration",
    "optional",
    "criticality",
    "preconnect",
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


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ServerConfig:
    """一个 MCP Server 的静态配置。"""

    server_id: str
    transport: Transport
    connection: dict[str, Any]
    description: str = ""
    enabled: bool = True
    preconnect: bool = False
    # 集成模式：用户自有后端项目封装成的 MCP，而非网络上的通用 MCP 服务。
    integration: bool = False
    criticality: str = "optional"
    namespace: str = ""
    allow_tools: list[str] = field(default_factory=list)
    deny_tools: list[str] = field(default_factory=list)
    connect_timeout_ms: int = 15000
    call_timeout_ms: int = 120000
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    health: HealthPolicy = field(default_factory=HealthPolicy)
    tags: list[str] = field(default_factory=list)
    namespace_mode: str = "prefix"
    namespace_separator: str = "__"

    @property
    def effective_namespace(self) -> str:
        return self.namespace or self.server_id

    @property
    def pool_key(self) -> str:
        """连接签名：决定哪些逻辑服务复用同一个物理连接。"""
        base = {
            "transport": self.transport.value,
            "connection": self.connection,
        }
        return f"{self.transport.value}:{_stable_hash(base)}"

    @property
    def config_signature(self) -> str:
        return _stable_hash(
            {
                "transport": self.transport.value,
                "connection": self.connection,
                "namespace": self.effective_namespace,
                "allow_tools": self.allow_tools,
                "deny_tools": self.deny_tools,
                "enabled": self.enabled,
                "preconnect": self.preconnect,
                "integration": self.integration,
            }
        )

    def tool_allowed(self, remote_name: str) -> bool:
        if remote_name in self.deny_tools:
            return False
        if self.allow_tools and remote_name not in self.allow_tools:
            return False
        return True

    def exposed_name(self, remote_name: str) -> str:
        """生成模型可见的工具名。

        逻辑名是 ``namespace.tool``，模型可见名使用 ``namespace__tool``，
        避免部分 OpenAI 兼容端点拒绝点号。
        """
        if self.namespace_mode == "legacy":
            return remote_name
        safe_namespace = _safe_identifier(self.effective_namespace)
        safe_tool = _safe_identifier(remote_name)
        return f"{safe_namespace}{self.namespace_separator}{safe_tool}"

    def public_dict(self, *, mask: bool = True) -> dict[str, Any]:
        connection = dict(self.connection)
        if mask:
            if connection.get("headers"):
                connection["headers"] = {
                    key: "***" for key in connection["headers"]
                }
            if connection.get("env"):
                # npm_config_cache 是运行时自动注入的项目内缓存路径，
                # 不回显也不落盘：避免把本机绝对路径写进配置，项目移动后失效。
                connection["env"] = {
                    key: "***"
                    for key in connection["env"]
                    if key.lower() != "npm_config_cache"
                }
        return {
            "server_id": self.server_id,
            "description": self.description,
            "transport": self.transport.value,
            "connection": connection,
            "enabled": self.enabled,
            "preconnect": self.preconnect,
            "integration": self.integration,
            "criticality": self.criticality,
            "namespace": self.effective_namespace,
            "allow_tools": self.allow_tools,
            "deny_tools": self.deny_tools,
            "connect_timeout_ms": self.connect_timeout_ms,
            "call_timeout_ms": self.call_timeout_ms,
            "tags": self.tags,
            "has_secret": bool(
                connection.get("headers") or connection.get("env")
            ),
        }


def _safe_identifier(value: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "")
    return cleaned or "server"


@dataclass
class ToolMeta:
    """一个 MCP 工具的元信息。"""

    server_id: str
    remote_name: str
    exposed_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    allowlisted: bool = True
    source: str = "live_discovery"

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "remote_name": self.remote_name,
            "exposed_name": self.exposed_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "allowlisted": self.allowlisted,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolMeta":
        return cls(
            server_id=str(value.get("server_id", "")),
            remote_name=str(value.get("remote_name", "")),
            exposed_name=str(value.get("exposed_name", "")),
            description=str(value.get("description", "")),
            input_schema=dict(value.get("input_schema") or {}),
            allowlisted=bool(value.get("allowlisted", True)),
            source=str(value.get("source", "lkg_cache")),
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ServerRuntime:
    """一个 MCP Server 的运行时状态（不持久化到配置文件）。"""

    config: ServerConfig
    state: ServerState = ServerState.DORMANT
    epoch: int = 0
    error: str = ""
    error_code: str = ""
    error_category: str = ""
    retry_at: str = ""
    retry_count: int = 0
    connected_at: str = ""
    last_used_at: str = ""
    latency_ms: int | None = None
    tool_count: int = 0
    tool_source: str = "none"
    pool_reused: bool = False
    health_failures: int = 0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        config = self.config
        return {
            **config.public_dict(),
            "state": self.state.value,
            "error": self.error,
            "error_code": self.error_code,
            "error_category": self.error_category,
            "retry_at": self.retry_at or None,
            "retry_count": self.retry_count,
            "epoch": self.epoch,
            "connected_at": self.connected_at or None,
            "last_used_at": self.last_used_at or None,
            "latency_ms": self.latency_ms,
            "tool_count": self.tool_count,
            "tool_source": self.tool_source,
            "pool_reused": self.pool_reused,
            "pool_key": config.pool_key[:23],
            "connected": self.state
            in {ServerState.CONNECTED, ServerState.DEGRADED},
        }
