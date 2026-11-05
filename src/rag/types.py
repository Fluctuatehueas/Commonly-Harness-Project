"""RAG Provider 的公共数据模型。

集成模式下 Harness 不保存知识原文、切片与向量，只保存 Provider 的连接
配置与运行状态；检索、灌入与知识管理都转发给甲方/用户自有 RAG 服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RagProtocol(str, Enum):
    """Provider 的接入协议。"""

    HTTP = "http"
    MCP = "mcp"


class RagProviderState(str, Enum):
    """Provider 的运行状态。"""

    UNKNOWN = "Unknown"
    READY = "Ready"
    DEGRADED = "Degraded"
    UNAVAILABLE = "Unavailable"
    DISABLED = "Disabled"


DEFAULT_MCP_TOOLS: dict[str, str] = {
    "retrieve": "rag_retrieve",
    "ingest": "rag_ingest",
    "list_documents": "rag_list_documents",
    "delete_document": "rag_delete_document",
    "stats": "rag_knowledge_stats",
    "health": "rag_health",
    "manifest": "rag_manifest",
}


class RagError(Exception):
    """带 Provider 上下文的结构化错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "RAG_ERROR",
        provider_id: str = "",
        http_status: int = 502,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.provider_id = provider_id
        self.http_status = http_status
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "provider_id": self.provider_id,
                "retryable": self.retryable,
            },
        }


@dataclass
class RagProviderConfig:
    """一个 RAG Provider 的静态配置。"""

    provider_id: str
    name: str
    protocol: RagProtocol
    description: str = ""
    endpoint: str = ""
    mcp_server_id: str = ""
    auth: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(
        default_factory=lambda: ["retrieve", "health"]
    )
    knowledge_domains: list[str] = field(default_factory=list)
    citation_format: str = "chunk"
    limits: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 30000
    enabled: bool = True
    source: str = "manual"  # manual | mcp_discovery
    integration_server_id: str = ""

    @property
    def base_url(self) -> str:
        return self.endpoint.rstrip("/")

    @property
    def top_k_max(self) -> int:
        value = self.limits.get("top_k_max", 20)
        try:
            return max(1, min(int(value), 50))
        except (TypeError, ValueError):
            return 20

    @property
    def max_chars_per_chunk(self) -> int:
        value = self.limits.get("max_chars_per_chunk", 2000)
        try:
            return max(1, min(int(value), 8000))
        except (TypeError, ValueError):
            return 2000

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def tool(self, capability: str) -> str:
        return self.tools.get(capability) or DEFAULT_MCP_TOOLS.get(capability, "")

    def public_dict(self, *, mask: bool = True) -> dict[str, Any]:
        auth = dict(self.auth or {})
        if mask and isinstance(auth.get("headers"), dict):
            auth["headers"] = {key: "***" for key in auth["headers"]}
        if mask and auth.get("token"):
            auth["token"] = "***"
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "description": self.description,
            "protocol": self.protocol.value,
            "endpoint": self.endpoint,
            "mcp_server_id": self.mcp_server_id,
            "auth": auth,
            "capabilities": list(self.capabilities),
            "knowledge_domains": list(self.knowledge_domains),
            "citation_format": self.citation_format,
            "limits": dict(self.limits),
            "tools": dict(self.tools),
            "timeout_ms": self.timeout_ms,
            "enabled": self.enabled,
            "source": self.source,
            "integration_server_id": self.integration_server_id,
            "has_secret": bool(
                auth.get("secret_ref") or auth.get("token") or auth.get("headers")
            ),
        }


@dataclass
class RagProviderRuntime:
    """一个 Provider 的运行状态（不持久化）。"""

    config: RagProviderConfig
    state: RagProviderState = RagProviderState.UNKNOWN
    last_error: str = ""
    last_checked_at: str = ""
    latency_ms: int | None = None
    document_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.config.public_dict(),
            "state": (
                RagProviderState.DISABLED.value
                if not self.config.enabled
                else self.state.value
            ),
            "ready": self.state == RagProviderState.READY and self.config.enabled,
            "last_error": self.last_error,
            "last_checked_at": self.last_checked_at or None,
            "latency_ms": self.latency_ms,
            "document_count": self.document_count,
        }


def clip_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if limit > 0 and len(text) > limit:
        return text[:limit] + "…"
    return text
