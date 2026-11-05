"""RAG Provider 管理 REST API。

注意：``/rag`` 是 Harness 的管理与适配接口，Harness 不落知识原文与向量。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from rag.runtime import rag_runtime
from rag.types import RagError

router = APIRouter(prefix="/rag", tags=["rag"])


class ProviderUpsert(BaseModel):
    """新增/更新一个 RAG Provider。"""

    provider_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field("", max_length=120)
    description: str = Field("", max_length=500)
    protocol: Literal["http", "mcp"] = "http"
    endpoint: str = Field("", max_length=2000)
    mcp_server_id: str = Field("", max_length=64)
    auth: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(
        default_factory=lambda: ["retrieve", "health"]
    )
    knowledge_domains: list[str] = Field(default_factory=list)
    citation_format: str = Field("chunk", max_length=32)
    limits: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = Field(30000, ge=1000, le=300000)
    enabled: bool = True
    integration_server_id: str = Field("", max_length=64)

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name or self.provider_id,
            "description": self.description,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "mcp_server_id": self.mcp_server_id,
            "auth": self.auth,
            "capabilities": self.capabilities,
            "knowledge_domains": self.knowledge_domains,
            "citation_format": self.citation_format,
            "limits": self.limits,
            "tools": self.tools,
            "timeout_ms": self.timeout_ms,
            "enabled": self.enabled,
            "integration_server_id": self.integration_server_id,
        }


class EnableRequest(BaseModel):
    enabled: bool


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=50)
    provider_ids: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    user_scope: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field("", max_length=128)


class IngestRequest(BaseModel):
    documents: list[dict[str, Any]] = Field(..., min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class DiscoverRequest(BaseModel):
    """从已接入的 MCP 后端发现 RAG Provider。"""

    mcp_server_id: str = Field(..., min_length=1, max_length=64)
    discovery_tool: str = Field("rag_manifest", min_length=1, max_length=128)
    auto_register: bool = True


def _http_error(error: RagError) -> HTTPException:
    return HTTPException(status_code=error.http_status, detail=error.to_dict()["error"])


def _not_found(provider_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "RAG_PROVIDER_NOT_FOUND",
            "message": f"RAG Provider 不存在: {provider_id}",
            "provider_id": provider_id,
        },
    )


@router.get("/config")
async def config_info() -> dict[str, Any]:
    return {
        "format": "json",
        "config_path": str(rag_runtime.store.path),
        "provider_count": len(rag_runtime.providers()),
    }


@router.get("/providers")
async def list_providers(
    protocol: str = Query("", max_length=16),
    enabled: bool | None = Query(None),
    integration_server_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """返回 Provider 运行态；不会建立任何连接。"""
    items = rag_runtime.details()
    if protocol:
        items = [
            item for item in items
            if str(item.get("protocol", "")).lower() == protocol.lower()
        ]
    if enabled is not None:
        items = [item for item in items if bool(item.get("enabled")) is enabled]
    if integration_server_id:
        items = [
            item for item in items
            if item.get("integration_server_id") == integration_server_id
        ]
    return {"items": items, "total": len(items)}


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict[str, Any]:
    detail = rag_runtime.detail(provider_id)
    if detail is None:
        raise _not_found(provider_id)
    return detail


@router.post("/providers")
async def create_provider(body: ProviderUpsert) -> dict[str, Any]:
    try:
        return await rag_runtime.upsert_provider(body.to_payload())
    except RagError as error:
        raise _http_error(error) from error


@router.put("/providers/{provider_id}")
async def update_provider(
    provider_id: str, body: ProviderUpsert,
) -> dict[str, Any]:
    if body.provider_id != provider_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RAG_CONFIG_INVALID",
                "message": "路径与请求体 provider_id 不一致",
            },
        )
    try:
        return await rag_runtime.upsert_provider(body.to_payload())
    except RagError as error:
        raise _http_error(error) from error


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> dict[str, bool]:
    try:
        await rag_runtime.delete_provider(provider_id)
    except RagError as error:
        raise _http_error(error) from error
    return {"ok": True}


@router.post("/providers/{provider_id}/enable")
async def enable_provider(
    provider_id: str, body: EnableRequest,
) -> dict[str, Any]:
    try:
        return await rag_runtime.set_enabled(provider_id, body.enabled)
    except RagError as error:
        raise _http_error(error) from error


@router.post("/providers/{provider_id}/probe")
async def probe_provider(provider_id: str) -> dict[str, Any]:
    try:
        return await rag_runtime.probe(provider_id)
    except RagError as error:
        raise _http_error(error) from error


@router.post("/retrieve")
async def retrieve(body: RetrieveRequest) -> dict[str, Any]:
    try:
        return await rag_runtime.retrieve(
            body.query,
            provider_ids=body.provider_ids or None,
            top_k=body.top_k,
            filters=body.filters,
            user_scope=body.user_scope,
            trace_id=body.trace_id,
        )
    except RagError as error:
        raise _http_error(error) from error


@router.post("/providers/{provider_id}/ingest")
async def ingest(
    provider_id: str, body: IngestRequest,
) -> dict[str, Any]:
    try:
        return await rag_runtime.ingest(
            provider_id, body.documents, options=body.options
        )
    except RagError as error:
        raise _http_error(error) from error


@router.get("/providers/{provider_id}/knowledge/documents")
async def list_documents(
    provider_id: str,
    limit: int = Query(50, ge=1, le=500),
    cursor: str = Query("", max_length=200),
) -> dict[str, Any]:
    try:
        return await rag_runtime.list_documents(
            provider_id, limit=limit, cursor=cursor
        )
    except RagError as error:
        raise _http_error(error) from error


@router.delete("/providers/{provider_id}/knowledge/documents/{document_id}")
async def delete_document(
    provider_id: str, document_id: str,
) -> dict[str, Any]:
    try:
        return await rag_runtime.delete_document(provider_id, document_id)
    except RagError as error:
        raise _http_error(error) from error


@router.get("/providers/{provider_id}/knowledge/stats")
async def knowledge_stats(provider_id: str) -> dict[str, Any]:
    try:
        return await rag_runtime.stats(provider_id)
    except RagError as error:
        raise _http_error(error) from error


@router.post("/discover")
async def discover(body: DiscoverRequest) -> dict[str, Any]:
    """让已接入的 MCP 后端自己上报 RAG manifest。"""
    try:
        return await rag_runtime.discover_from_mcp(
            body.mcp_server_id,
            discovery_tool=body.discovery_tool,
            register=body.auto_register,
        )
    except RagError as error:
        raise _http_error(error) from error
