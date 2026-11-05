"""RAG Provider 运行时：注册、健康检查、检索与知识管理转发。

Harness 只做编排与引用归一化，不保存知识原文、切片或向量。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from agent.config import RAG_CONFIG_PATH
from agent.mcp.runtime import mcp_runtime
from rag.config_store import RagConfigStore, build_provider, provider_auth_headers
from rag.types import (
    RagError,
    RagProtocol,
    RagProviderConfig,
    RagProviderRuntime,
    RagProviderState,
    clip_text,
)

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RagRuntime:
    """RAG Provider 的进程内运行时。"""

    def __init__(self, store: RagConfigStore | None = None) -> None:
        self.store = store or RagConfigStore(RAG_CONFIG_PATH)
        self._providers: dict[str, RagProviderConfig] = {}
        self._states: dict[str, RagProviderRuntime] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            self._reload()
            self._initialized = True

    def _reload(self) -> None:
        providers = self.store.load()
        self._providers = providers
        states: dict[str, RagProviderRuntime] = {}
        for provider_id, config in providers.items():
            previous = self._states.get(provider_id)
            if previous is not None and previous.config == config:
                states[provider_id] = previous
            else:
                states[provider_id] = RagProviderRuntime(
                    config=config,
                    state=(
                        RagProviderState.UNKNOWN
                        if config.enabled
                        else RagProviderState.DISABLED
                    ),
                )
        self._states = states

    def reload(self) -> None:
        self._reload()

    def _ensure_loaded(self) -> None:
        """同步查询接口也要读到注册表，不能依赖调用方先执行 initialize。"""
        if not self._initialized:
            self._reload()
            self._initialized = True

    # ---------- 查询 ----------

    def provider(self, provider_id: str) -> RagProviderConfig | None:
        self._ensure_loaded()
        return self._providers.get(provider_id)

    def providers(self) -> list[RagProviderConfig]:
        self._ensure_loaded()
        return list(self._providers.values())

    def runtime_for(self, provider_id: str) -> RagProviderRuntime | None:
        self._ensure_loaded()
        return self._states.get(provider_id)

    def details(self) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return [
            runtime.to_dict() for runtime in self._states.values()
        ]

    def detail(self, provider_id: str) -> dict[str, Any] | None:
        self._ensure_loaded()
        runtime = self._states.get(provider_id)
        return runtime.to_dict() if runtime else None

    # ---------- 管理 ----------

    async def upsert_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        config = self.store.upsert(payload)
        self._providers[config.provider_id] = config
        self._states[config.provider_id] = RagProviderRuntime(
            config=config,
            state=(
                RagProviderState.UNKNOWN
                if config.enabled
                else RagProviderState.DISABLED
            ),
        )
        return self.detail(config.provider_id) or {}

    async def delete_provider(self, provider_id: str) -> None:
        await self.initialize()
        self.store.delete(provider_id)
        self._providers.pop(provider_id, None)
        self._states.pop(provider_id, None)

    async def set_enabled(
        self, provider_id: str, enabled: bool,
    ) -> dict[str, Any]:
        await self.initialize()
        config = self.store.set_enabled(provider_id, enabled)
        self._providers[provider_id] = config
        state = self._states.get(provider_id)
        if state is None:
            state = RagProviderRuntime(config=config)
            self._states[provider_id] = state
        state.config = config
        if not enabled:
            state.state = RagProviderState.DISABLED
            state.last_error = ""
        return self.detail(provider_id) or {}

    # ---------- 健康 ----------

    async def probe(self, provider_id: str) -> dict[str, Any]:
        await self.initialize()
        config = self._providers.get(provider_id)
        state = self._states.get(provider_id)
        if config is None or state is None:
            raise RagError(
                f"RAG Provider 不存在: {provider_id}",
                code="RAG_PROVIDER_NOT_FOUND",
                provider_id=provider_id,
                http_status=404,
            )
        if not config.enabled:
            state.state = RagProviderState.DISABLED
            return self.detail(provider_id) or {}

        started = time.monotonic()
        try:
            if config.protocol is RagProtocol.MCP:
                await self._probe_mcp(config)
            else:
                await self._http_json(config, "GET", "/health")
            state.state = RagProviderState.READY
            state.last_error = ""
        except RagError as error:
            state.state = RagProviderState.UNAVAILABLE
            state.last_error = error.message
        except Exception as error:  # noqa: BLE001
            state.state = RagProviderState.UNAVAILABLE
            state.last_error = f"{type(error).__name__}: {error}"
        finally:
            state.latency_ms = int((time.monotonic() - started) * 1000)
            state.last_checked_at = _utc_now()
        return self.detail(provider_id) or {}

    async def _probe_mcp(self, config: RagProviderConfig) -> None:
        server_id = config.mcp_server_id
        server_config = mcp_runtime.config_for(server_id)
        if server_config is None:
            raise RagError(
                f"MCP 服务不存在: {server_id}",
                code="RAG_MCP_SERVER_NOT_FOUND",
                provider_id=config.provider_id,
                http_status=404,
            )
        state = mcp_runtime.state_for(server_id)
        if state.value not in {"Connected", "Degraded"}:
            await mcp_runtime.probe(server_id)
        state = mcp_runtime.state_for(server_id)
        if state.value not in {"Connected", "Degraded"}:
            raise RagError(
                f"RAG 后端 MCP 未连接（{state.value}）",
                code="RAG_BACKEND_UNAVAILABLE",
                provider_id=config.provider_id,
                http_status=503,
            )

    # ---------- 检索 ----------

    async def retrieve(
        self,
        query: str,
        *,
        provider_ids: list[str] | None = None,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        user_scope: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> dict[str, Any]:
        await self.initialize()
        text = str(query or "").strip()
        if not text:
            raise RagError(
                "检索 query 不能为空",
                code="INVALID_ARGUMENT",
                http_status=422,
            )

        selected = self._select_providers(provider_ids, capability="retrieve")
        if not selected:
            return {
                "query": text,
                "top_k": max(1, min(int(top_k or 5), 20)),
                "chunks": [],
                "providers": [],
                "errors": [],
                "note": "没有已注册且启用的 RAG Provider",
            }

        chunks: list[dict[str, Any]] = []
        used: list[str] = []
        errors: list[dict[str, str]] = []
        for config in selected:
            limit = max(1, min(int(top_k or 5), config.top_k_max))
            try:
                raw_chunks = await self._retrieve_one(
                    config,
                    query=text,
                    top_k=limit,
                    filters=filters or {},
                    user_scope=user_scope or {},
                    trace_id=trace_id,
                )
                chunks.extend(
                    self._normalize_chunks(config, raw_chunks)
                )
                used.append(config.provider_id)
                state = self._states.get(config.provider_id)
                if state is not None:
                    state.state = RagProviderState.READY
                    state.last_error = ""
                    state.last_checked_at = _utc_now()
            except Exception as error:  # noqa: BLE001
                message = (
                    error.message if isinstance(error, RagError) else str(error)
                )
                errors.append(
                    {"provider_id": config.provider_id, "message": message}
                )
                state = self._states.get(config.provider_id)
                if state is not None:
                    state.state = RagProviderState.UNAVAILABLE
                    state.last_error = message

        if not used and errors:
            first = errors[0]
            raise RagError(
                f"RAG 检索失败：{first['message']}",
                code="RAG_UNAVAILABLE",
                provider_id=first["provider_id"],
                http_status=503,
                retryable=True,
            )

        return {
            "query": text,
            "top_k": max(1, min(int(top_k or 5), 20)),
            "chunks": chunks,
            "providers": used,
            "errors": errors,
        }

    async def _retrieve_one(
        self,
        config: RagProviderConfig,
        *,
        query: str,
        top_k: int,
        filters: dict[str, Any],
        user_scope: dict[str, Any],
        trace_id: str,
    ) -> Any:
        payload = {
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "user_scope": user_scope,
            "trace_id": trace_id,
        }
        if config.protocol is RagProtocol.HTTP:
            data = await self._http_json(config, "POST", "/retrieve", payload)
            return data.get("chunks") if isinstance(data, dict) else data
        result = await self._call_mcp_tool(config, "retrieve", payload)
        if isinstance(result, dict):
            return result.get("chunks") or result.get("results") or result
        return result

    def _normalize_chunks(
        self, config: RagProviderConfig, raw: Any,
    ) -> list[dict[str, Any]]:
        if raw is None:
            return []
        items = raw if isinstance(raw, list) else (
            raw.get("chunks") if isinstance(raw, dict) else [raw]
        )
        if not isinstance(items, list):
            items = [items]

        chunks: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                item = {"content": str(item)}
            raw_citation = str(item.get("citation_id") or f"c{index + 1}")
            citation_id = f"{config.provider_id}:{raw_citation}"
            source = item.get("source")
            if not isinstance(source, dict):
                source = {"title": str(source or config.name)}
            chunks.append(
                {
                    "citation_id": citation_id,
                    "content": clip_text(
                        item.get("content") or item.get("text"),
                        config.max_chars_per_chunk,
                    ),
                    "score": item.get("score"),
                    "source": source,
                    "metadata": item.get("metadata") or {},
                    "provider_id": config.provider_id,
                    "provider_name": config.name,
                }
            )
        return chunks

    # ---------- 知识灌入与管理 ----------

    async def ingest(
        self,
        provider_id: str,
        documents: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        config = self._require_capability(provider_id, "ingest")
        payload = {"documents": documents, "options": options or {}}
        if config.protocol is RagProtocol.HTTP:
            result = await self._http_json(
                config, "POST", "/knowledge/ingest", payload
            )
        else:
            result = await self._call_mcp_tool(config, "ingest", payload)
        return {
            "ok": True,
            "provider_id": provider_id,
            "result": result,
        }

    async def list_documents(
        self,
        provider_id: str,
        *,
        limit: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        await self.initialize()
        config = self._require_capability(provider_id, "list_documents")
        payload = {"limit": limit, "cursor": cursor}
        if config.protocol is RagProtocol.HTTP:
            result = await self._http_json(
                config, "GET", "/knowledge/documents", params=payload
            )
        else:
            result = await self._call_mcp_tool(
                config, "list_documents", payload
            )
        if isinstance(result, dict):
            documents = (
                result.get("documents")
                or result.get("items")
                or result.get("results")
                or []
            )
            return {"provider_id": provider_id, "documents": documents, **result}
        return {"provider_id": provider_id, "documents": result}

    async def delete_document(
        self, provider_id: str, document_id: str,
    ) -> dict[str, Any]:
        await self.initialize()
        config = self._require_capability(provider_id, "delete_document")
        if config.protocol is RagProtocol.HTTP:
            result = await self._http_json(
                config,
                "DELETE",
                f"/knowledge/documents/{document_id}",
            )
        else:
            result = await self._call_mcp_tool(
                config, "delete_document", {"document_id": document_id}
            )
        self._refresh_document_count(provider_id)
        return {"ok": True, "provider_id": provider_id, "result": result}

    async def stats(self, provider_id: str) -> dict[str, Any]:
        await self.initialize()
        config = self._require_capability(provider_id, "stats")
        if config.protocol is RagProtocol.HTTP:
            result = await self._http_json(
                config, "GET", "/knowledge/stats"
            )
        else:
            result = await self._call_mcp_tool(config, "stats", {})
        state = self._states.get(provider_id)
        if state is not None and isinstance(result, dict):
            count = result.get("document_count") or result.get("documents")
            if isinstance(count, int):
                state.document_count = count
        return {"provider_id": provider_id, "stats": result}

    # ---------- MCP 发现 ----------

    async def discover_from_mcp(
        self,
        server_id: str,
        *,
        discovery_tool: str = "rag_manifest",
        register: bool = True,
    ) -> dict[str, Any]:
        """调用 MCP 服务上的 RAG 发现工具，把 manifest 注册为 Provider。"""
        await self.initialize()
        if mcp_runtime.config_for(server_id) is None:
            raise RagError(
                f"MCP 服务不存在: {server_id}",
                code="RAG_MCP_SERVER_NOT_FOUND",
                provider_id=server_id,
                http_status=404,
            )
        text = await mcp_runtime.call_tool(server_id, discovery_tool, {})
        data = self._parse_json_text(text)
        if isinstance(data, dict) and data.get("ok") is False:
            raise RagError(
                f"RAG 发现失败：{data.get('error', {}).get('message', text)}",
                code="RAG_DISCOVERY_FAILED",
                provider_id=server_id,
                http_status=502,
            )

        manifests = data.get("providers") if isinstance(data, dict) else None
        if manifests is None and isinstance(data, dict) and data.get("provider"):
            manifests = [data["provider"]]
        if manifests is None:
            manifests = [data] if isinstance(data, dict) else data
        if not isinstance(manifests, list):
            raise RagError(
                "RAG manifest 格式不正确：需要对象或对象数组",
                code="RAG_MANIFEST_INVALID",
                provider_id=server_id,
                http_status=422,
            )

        discovered: list[dict[str, Any]] = []
        for manifest in manifests:
            if not isinstance(manifest, dict):
                continue
            payload = {
                **manifest,
                "protocol": manifest.get("protocol") or "mcp",
                "mcp_server_id": server_id,
                "integration_server_id": server_id,
                "source": "mcp_discovery",
            }
            if not payload.get("provider_id"):
                payload["provider_id"] = f"rag_{server_id}"
            config = build_provider(payload, source="mcp_discovery")
            if register:
                detail = await self.upsert_provider(
                    {
                        **payload,
                        "provider_id": config.provider_id,
                    }
                )
                discovered.append(detail)
            else:
                discovered.append(config.public_dict())
        return {
            "server_id": server_id,
            "discovered": discovered,
            "registered": bool(register),
        }

    # ---------- 内部工具 ----------

    def _select_providers(
        self,
        provider_ids: list[str] | None,
        *,
        capability: str,
    ) -> list[RagProviderConfig]:
        ids = provider_ids or list(self._providers)
        result: list[RagProviderConfig] = []
        for provider_id in ids:
            config = self._providers.get(provider_id)
            if config is None or not config.enabled:
                continue
            if not config.supports(capability):
                continue
            result.append(config)
        return result

    def _require_capability(
        self, provider_id: str, capability: str,
    ) -> RagProviderConfig:
        config = self._providers.get(provider_id)
        if config is None:
            raise RagError(
                f"RAG Provider 不存在: {provider_id}",
                code="RAG_PROVIDER_NOT_FOUND",
                provider_id=provider_id,
                http_status=404,
            )
        if not config.enabled:
            raise RagError(
                f"RAG Provider 已停用: {provider_id}",
                code="RAG_PROVIDER_DISABLED",
                provider_id=provider_id,
                http_status=422,
            )
        if not config.supports(capability):
            raise RagError(
                f"RAG Provider 不支持能力: {capability}",
                code="RAG_UNSUPPORTED",
                provider_id=provider_id,
                http_status=422,
            )
        return config

    async def _http_json(
        self,
        config: RagProviderConfig,
        method: str,
        path: str,
        payload: Any = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{config.base_url}{path}"
        headers = provider_auth_headers(config)
        timeout = max(1.0, config.timeout_ms / 1000)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=payload if method != "GET" else None,
                    params=params,
                )
        except httpx.HTTPError as error:
            raise RagError(
                f"RAG 服务不可达：{type(error).__name__}",
                code="RAG_BACKEND_UNAVAILABLE",
                provider_id=config.provider_id,
                http_status=503,
                retryable=True,
            ) from error
        if response.status_code >= 400:
            detail = response.text[:400]
            raise RagError(
                f"RAG 服务返回 HTTP {response.status_code}：{detail}",
                code="RAG_BACKEND_ERROR",
                provider_id=config.provider_id,
                http_status=(
                    504 if response.status_code == 504 else 502
                ),
                retryable=response.status_code in {429, 502, 503, 504},
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    async def _call_mcp_tool(
        self,
        config: RagProviderConfig,
        capability: str,
        arguments: dict[str, Any],
    ) -> Any:
        tool_name = config.tool(capability)
        if not tool_name:
            raise RagError(
                f"MCP RAG Provider 未配置 {capability} 工具名",
                code="RAG_CONFIG_INVALID",
                provider_id=config.provider_id,
                http_status=422,
            )
        arguments = self._mcp_arguments(
            config.mcp_server_id, tool_name, arguments
        )
        text = await mcp_runtime.call_tool(
            config.mcp_server_id, tool_name, arguments
        )
        data = self._parse_json_text(text)
        if isinstance(data, dict) and data.get("ok") is False:
            error = data.get("error") or {}
            raise RagError(
                str(error.get("message") or "MCP RAG 工具调用失败"),
                code=str(error.get("code") or "RAG_MCP_TOOL_FAILED"),
                provider_id=config.provider_id,
                http_status=502,
                retryable=bool(error.get("retryable")),
            )
        return data

    def _mcp_arguments(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """按 MCP 工具的 inputSchema 裁剪参数。

        外部 MCP 工具的参数模型可能比 Harness 的通用契约更窄，
        多余字段会被服务端拒绝。已知 Schema 时只发送它声明接受的字段；
        未知 Schema 时退化为丢弃空值，避免把空 filters/trace_id 塞给服务端。
        """
        metas = mcp_runtime.registry.tool_metas(server_id)
        meta = next(
            (item for item in metas if item.remote_name == tool_name), None
        )
        properties = None
        if meta is not None and isinstance(meta.input_schema, dict):
            properties = meta.input_schema.get("properties")
        if isinstance(properties, dict):
            allowed = set(properties)
            return {
                key: value
                for key, value in arguments.items()
                if key in allowed
            }
        return {
            key: value
            for key, value in arguments.items()
            if value not in ({}, "", None)
        }

    @staticmethod
    def _parse_json_text(text: Any) -> Any:
        if not isinstance(text, str):
            return text
        value = text.strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"content": text}
        # 部分 MCP Server（如 Spring AI）会把工具返回的字符串再 JSON 编码一次，
        # 形成 "\"{...}\"" 的双层结构；这里循环解包到真正的对象。
        for _ in range(2):
            if not isinstance(parsed, str):
                break
            candidate = parsed.strip()
            if not candidate:
                break
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                break
        return parsed

    def _refresh_document_count(self, provider_id: str) -> None:
        state = self._states.get(provider_id)
        if state is not None and state.document_count is not None:
            state.document_count = max(0, state.document_count - 1)


rag_runtime = RagRuntime()
