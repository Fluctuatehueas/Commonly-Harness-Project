"""RAG Provider 注册表：JSON 配置的唯一读写入口。

与 MCP 配置一致：原子写入、写前校验、密钥掩码、``${ENV}`` 展开。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from rag.types import (
    DEFAULT_MCP_TOOLS,
    RagError,
    RagProtocol,
    RagProviderConfig,
)

logger = logging.getLogger(__name__)

PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
CAPABILITIES = {"retrieve", "ingest", "health", "list_documents", "delete_document", "stats"}

DEFAULT_DOC: dict[str, Any] = {"version": 1, "providers": {}}


def _config_error(message: str, *, provider_id: str = "") -> RagError:
    return RagError(
        message,
        code="RAG_CONFIG_INVALID",
        provider_id=provider_id,
        http_status=422,
    )


def _expand_env(value: str) -> str:
    return ENV_PATTERN.sub(
        lambda match: os.getenv(match.group(1), match.group(2) or ""),
        str(value),
    )


def _expand_mapping(mapping: dict[str, Any] | None) -> dict[str, str]:
    return {
        str(key): _expand_env(str(value))
        for key, value in (mapping or {}).items()
    }


def _as_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def normalize_provider_id(value: str, *, fallback: str = "rag_provider") -> str:
    raw = str(value or "").strip() or fallback
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:64]
    if not normalized:
        normalized = fallback
    if not normalized[0].isalnum():
        normalized = f"rag-{normalized}"[:64]
    return normalized


def _parse_protocol(value: Any) -> RagProtocol:
    protocol = str(value or "").strip().lower()
    if protocol in {"mcp", "mcp_sse", "streamable_http", "streamable-http"}:
        return RagProtocol.MCP
    if protocol in {"http", "https", "openapi", "rest"}:
        return RagProtocol.HTTP
    raise _config_error(f"不支持的 RAG protocol: {value!r}")


def build_provider(raw: dict[str, Any], *, source: str = "manual") -> RagProviderConfig:
    """把配置字典转换为强类型 Provider 配置。"""
    if not isinstance(raw, dict):
        raise _config_error("RAG Provider 配置必须是对象")

    provider_id = normalize_provider_id(str(raw.get("provider_id") or ""))
    if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise _config_error(f"非法 provider_id: {provider_id}", provider_id=provider_id)

    name = str(raw.get("name") or provider_id).strip()[:120]
    protocol = _parse_protocol(raw.get("protocol") or "http")

    endpoint = str(raw.get("endpoint") or "").strip()
    mcp_server_id = str(raw.get("mcp_server_id") or "").strip()
    if protocol is RagProtocol.HTTP:
        if not endpoint.startswith(("http://", "https://")):
            raise _config_error(
                "HTTP RAG Provider 缺少合法的 http(s) endpoint",
                provider_id=provider_id,
            )
    elif not mcp_server_id:
        raise _config_error(
            "MCP RAG Provider 必须引用一个已配置的 mcp_server_id",
            provider_id=provider_id,
        )

    auth_raw = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    auth = {
        key: value for key, value in auth_raw.items()
    }
    if isinstance(auth.get("headers"), dict):
        auth["headers"] = _expand_mapping(auth["headers"])

    capabilities_raw = raw.get("capabilities")
    capabilities = (
        [str(item) for item in capabilities_raw]
        if isinstance(capabilities_raw, list)
        else ["retrieve", "health"]
    )
    capabilities = [
        item for item in dict.fromkeys(capabilities) if item in CAPABILITIES
    ]
    if "retrieve" not in capabilities:
        capabilities.insert(0, "retrieve")

    domains_raw = raw.get("knowledge_domains")
    knowledge_domains = (
        [str(item) for item in domains_raw]
        if isinstance(domains_raw, list)
        else []
    )

    limits_raw = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    limits = {
        "top_k_max": _as_int(limits_raw.get("top_k_max"), 20),
        "max_chars_per_chunk": _as_int(
            limits_raw.get("max_chars_per_chunk"), 2000
        ),
    }
    if limits_raw.get("qps") is not None:
        limits["qps"] = _as_int(limits_raw.get("qps"), 10)

    tools_raw = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
    tools = {
        str(key): str(value)
        for key, value in tools_raw.items()
        if str(value).strip()
    }
    for key, default in DEFAULT_MCP_TOOLS.items():
        tools.setdefault(key, default)

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _config_error("enabled 必须是布尔值", provider_id=provider_id)

    return RagProviderConfig(
        provider_id=provider_id,
        name=name,
        protocol=protocol,
        description=str(raw.get("description") or "")[:500],
        endpoint=endpoint,
        mcp_server_id=mcp_server_id,
        auth=auth,
        capabilities=capabilities,
        knowledge_domains=knowledge_domains,
        citation_format=str(raw.get("citation_format") or "chunk"),
        limits=limits,
        tools=tools,
        timeout_ms=_as_int(raw.get("timeout_ms"), 30000, minimum=1000),
        enabled=enabled,
        source=str(raw.get("source") or source),
        integration_server_id=(
            str(raw.get("integration_server_id") or "").strip()
            or mcp_server_id
            if protocol is RagProtocol.MCP
            else str(raw.get("integration_server_id") or "").strip()
        ),
    )


class RagConfigStore:
    """RAG Provider 注册表的存储实现。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cache: dict[str, RagProviderConfig] | None = None

    def load(self) -> dict[str, RagProviderConfig]:
        if not self.path.exists():
            self._cache = {}
            return self._cache
        raw = self._read_json()
        providers_raw = raw.get("providers", {})
        if not isinstance(providers_raw, dict):
            raise _config_error("providers 必须是对象")
        providers: dict[str, RagProviderConfig] = {}
        for provider_id, provider_raw in providers_raw.items():
            if not isinstance(provider_raw, dict):
                raise _config_error(
                    f"Provider {provider_id} 的配置必须是对象",
                    provider_id=str(provider_id),
                )
            config = build_provider(
                {**provider_raw, "provider_id": provider_raw.get("provider_id") or provider_id}
            )
            providers[config.provider_id] = config
        self._cache = providers
        return providers

    def current(self) -> dict[str, RagProviderConfig]:
        return self._cache if self._cache is not None else self.load()

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_DOC)
        raw = self._read_json()
        raw.setdefault("version", 1)
        raw.setdefault("providers", {})
        return raw

    def _read_json(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig") or "{}")
        except json.JSONDecodeError as error:
            raise _config_error(f"RAG Provider JSON 解析失败: {error}") from error
        if not isinstance(raw, dict):
            raise _config_error("RAG Provider 配置必须是 JSON 对象")
        return raw

    def upsert(self, payload: dict[str, Any]) -> RagProviderConfig:
        config = build_provider(payload)
        raw = self._read_raw()
        providers = raw.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise _config_error("providers 必须是对象")
        existing = providers.get(config.provider_id)
        if existing is not None:
            payload = self._preserve_masked(payload, existing)
        providers[config.provider_id] = payload
        self._validate(raw)
        self._write(raw)
        self._cache = None
        return build_provider(providers[config.provider_id])

    def delete(self, provider_id: str) -> None:
        raw = self._read_raw()
        providers = raw.get("providers", {})
        if not isinstance(providers, dict) or provider_id not in providers:
            raise RagError(
                f"RAG Provider 不存在: {provider_id}",
                code="RAG_PROVIDER_NOT_FOUND",
                provider_id=provider_id,
                http_status=404,
            )
        providers.pop(provider_id)
        self._validate(raw)
        self._write(raw)
        self._cache = None

    def set_enabled(self, provider_id: str, enabled: bool) -> RagProviderConfig:
        raw = self._read_raw()
        providers = raw.get("providers", {})
        if not isinstance(providers, dict) or provider_id not in providers:
            raise RagError(
                f"RAG Provider 不存在: {provider_id}",
                code="RAG_PROVIDER_NOT_FOUND",
                provider_id=provider_id,
                http_status=404,
            )
        providers[provider_id]["enabled"] = bool(enabled)
        self._validate(raw)
        self._write(raw)
        self._cache = None
        return build_provider(providers[provider_id])

    def _validate(self, raw: dict[str, Any]) -> None:
        providers = raw.get("providers", {})
        if not isinstance(providers, dict):
            raise _config_error("providers 必须是对象")
        for provider_id, provider_raw in providers.items():
            build_provider(
                {**provider_raw, "provider_id": provider_raw.get("provider_id") or provider_id}
            )

    @staticmethod
    def _preserve_masked(incoming: Any, existing: Any) -> Any:
        if isinstance(incoming, dict):
            result = dict(incoming)
            existing_dict = existing if isinstance(existing, dict) else {}
            for key, value in incoming.items():
                if value == "***" and key in existing_dict:
                    result[key] = existing_dict[key]
                elif isinstance(value, dict):
                    result[key] = RagConfigStore._preserve_masked(
                        value, existing_dict.get(key)
                    )
            return result
        return incoming

    def _write(self, raw: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".providers.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        try:
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            os.replace(tmp_path, self.path)
        finally:
            tmp_path.unlink(missing_ok=True)
        logger.info("RAG Provider 配置已写入: %s", self.path)


def provider_auth_headers(config: RagProviderConfig) -> dict[str, str]:
    """解析 Provider 鉴权配置为 HTTP Header；密钥只从环境变量读取。"""
    headers: dict[str, str] = {"Accept": "application/json"}
    auth = config.auth or {}
    if isinstance(auth.get("headers"), dict):
        headers.update(_expand_mapping(auth["headers"]))

    auth_type = str(auth.get("type") or "").strip().lower()
    secret_ref = str(auth.get("secret_ref") or "").strip()
    if secret_ref:
        # secret_ref 可以是环境变量名，也可以是 ${VAR} 形式。
        token = os.getenv(secret_ref, "") or _expand_env(secret_ref)
    else:
        token = str(auth.get("token") or "")
    if not token:
        return headers
    if auth_type in {"bearer", "jwt", "oauth"}:
        headers.setdefault("Authorization", f"Bearer {token}")
    elif auth_type in {"api_key", "apikey", "key"}:
        header_name = str(auth.get("header") or "X-API-Key")
        headers.setdefault(header_name, token)
    return headers
