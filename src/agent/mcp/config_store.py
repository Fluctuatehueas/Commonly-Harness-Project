"""MCP 服务 JSON 配置的唯一读写入口。

职责：
- 读取 / 写回 ``mcp/servers.json``（所有服务统一放在一个 JSON 文件里）；
- 首次启动时把旧版 ``mcp_servers.yaml`` 自动迁移为 JSON；
- 解析 MCP 厂商提供的配置 JSON（如 ``{"mcpServers": {...}}``）；
- 校验服务、命名空间、引用与连接参数；
- 展开 ``${ENV}`` 占位符；
- 原子写入，失败不破坏原文件；
- 对外返回时对密钥做掩码。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.config import mcp_npm_cache_dir
from agent.mcp.types import (
    LOCAL_CONFIG_KEYS,
    ErrorCategory,
    HealthPolicy,
    McpError,
    NamespacePolicy,
    PoolPolicy,
    RetryPolicy,
    ServerConfig,
    Transport,
)


logger = logging.getLogger(__name__)

SERVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULT_DOC: dict[str, Any] = {
    "version": 2,
    "defaults": {
        "connect_timeout_ms": 15000,
        "call_timeout_ms": 120000,
        "retry": {
            "max_attempts": 4,
            "base_delay_ms": 1000,
            "max_delay_ms": 30000,
            "jitter": 0.2,
        },
        "health_check": {
            "interval_ms": 30000,
            "timeout_ms": 5000,
            "failure_threshold": 3,
            "success_threshold": 1,
        },
        "pool": {
            "idle_ttl_ms": 300000,
            "max_pool_size": 32,
            "connect_singleflight": True,
        },
        "namespace": {"mode": "prefix", "separator": "__"},
        "allow_tools": [],
        "deny_tools": [],
    },
    "servers": {},
    "main_servers": [],
}


@dataclass
class McpConfig:
    """一次加载后的完整配置快照。"""

    version: int
    servers: dict[str, ServerConfig]
    main_servers: list[str]
    raw: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def server_ids(self, *, enabled_only: bool = False) -> list[str]:
        ids = list(self.servers)
        if enabled_only:
            return [sid for sid in ids if self.servers[sid].enabled]
        return ids


def _config_error(message: str, *, server_id: str = "") -> McpError:
    return McpError(
        message,
        code="MCP_CONFIG_INVALID",
        category=ErrorCategory.CONFIG,
        server_id=server_id,
        retryable=False,
        http_status=422,
    )


def _expand_env(value: str) -> str:
    """展开 ``${VAR}`` / ``${VAR:-default}``。未配置时使用默认值或空串。"""
    return ENV_PATTERN.sub(
        lambda match: os.getenv(match.group(1), match.group(2) or ""),
        value,
    )


def _expand_mapping(mapping: dict[str, Any] | None) -> dict[str, str]:
    return {
        str(key): _expand_env(str(value))
        for key, value in (mapping or {}).items()
    }


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


_NPM_LAUNCH_COMMANDS = {"npx", "npm"}


def _inject_npm_cache(server_id: str, connection: dict[str, Any]) -> None:
    """npx / npm 型 stdio 服务统一使用项目内的 npm 缓存。

    系统 npm 缓存经常落在安装目录（如 ``D:\\nodejs\\node_cache``），普通用户
    没有写权限，``npx -y`` 首次下载包时会以 EPERM 退出，表现为服务“连不上”。
    这里把缓存定向到 ``<MCP 工作目录>/<server_id>/npm-cache``：既避开权限问题，
    也让服务配置、下载的包和产物集中在同一个目录。用户显式配置
    ``npm_config_cache`` 时保持原值。
    """
    command = str(connection.get("command") or "")
    if Path(command).stem.lower() not in _NPM_LAUNCH_COMMANDS:
        return
    env = dict(connection.get("env") or {})
    if any(str(key).lower() == "npm_config_cache" for key in env):
        return
    env["npm_config_cache"] = str(mcp_npm_cache_dir(server_id))
    connection["env"] = env


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _merge_dicts(base: Any, override: Any) -> dict[str, Any]:
    """递归合并配置对象，让 defaults 的嵌套字段不会被子项整体覆盖。"""
    if not isinstance(base, dict):
        return dict(override) if isinstance(override, dict) else {}
    result = dict(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _parse_retry(value: Any) -> RetryPolicy:
    default = RetryPolicy()
    if not isinstance(value, dict):
        return default
    retry_on = value.get("retry_on")
    return RetryPolicy(
        max_attempts=_as_int(value.get("max_attempts"), default.max_attempts, minimum=1),
        base_delay_ms=_as_int(value.get("base_delay_ms"), default.base_delay_ms, minimum=0),
        max_delay_ms=_as_int(value.get("max_delay_ms"), default.max_delay_ms, minimum=0),
        jitter=max(0.0, min(1.0, _as_float(value.get("jitter"), default.jitter))),
        retry_on=tuple(retry_on) if isinstance(retry_on, list) else default.retry_on,
    )


def _parse_health(value: Any) -> HealthPolicy:
    default = HealthPolicy()
    if not isinstance(value, dict):
        return default
    return HealthPolicy(
        interval_ms=_as_int(value.get("interval_ms"), default.interval_ms, minimum=1000),
        timeout_ms=_as_int(value.get("timeout_ms"), default.timeout_ms, minimum=500),
        failure_threshold=_as_int(
            value.get("failure_threshold"), default.failure_threshold, minimum=1
        ),
        success_threshold=_as_int(
            value.get("success_threshold"), default.success_threshold, minimum=1
        ),
        degraded_latency_ms=_as_int(
            value.get("degraded_latency_ms"), default.degraded_latency_ms, minimum=0
        ),
    )


def _normalize_transport(value: Any) -> Transport:
    transport = str(value or "").strip().lower()
    if transport in {"streamable-http", "streamable_http", "http", "streamablehttp"}:
        return Transport.STREAMABLE_HTTP
    if transport == "sse":
        return Transport.SSE
    if transport == "stdio":
        return Transport.STDIO
    raise _config_error(f"不支持的 transport: {value!r}")


# ============================================================
# 厂商配置 JSON 解析
# ============================================================

# 不同 MCP 客户端提供的容器字段名。
VENDOR_CONTAINER_KEYS = ("mcpServers", "mcp_servers", "servers")
VENDOR_URL_KEYS = ("url", "serverUrl", "server_url", "httpUrl", "http_url")


def normalize_server_id(name: str, *, fallback: str = "mcp") -> str:
    """把厂商给的任意服务名清洗成合法 server_id。"""
    raw = str(name or "").strip() or fallback
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:64]
    if not normalized:
        normalized = fallback
    if not normalized[0].isalnum():
        normalized = f"mcp-{normalized}"[:64]
    return normalized


def extract_vendor_servers(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    """从厂商 JSON 中提取 ``(server_id, 原始配置)`` 列表。

    支持：
    - ``{"mcpServers": {"name": {...}}}``（Claude Desktop / Cursor 等）
    - ``{"servers": {...}}``（Harness 自身配置）
    - 直接的服务映射 ``{"name": {...}}``
    - 单个服务对象 ``{"command": ..., "args": [...]}``
    """
    if not isinstance(payload, dict):
        raise _config_error("MCP 配置必须是 JSON 对象")

    mapping: dict[str, Any] | None = None
    for key in VENDOR_CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            mapping = value
            break
    if mapping is None:
        if any(key in payload for key in ("command", *VENDOR_URL_KEYS)):
            name = str(
                payload.get("name")
                or payload.get("server_id")
                or payload.get("id")
                or "mcp"
            )
            return [(normalize_server_id(name), payload)]
        mapping = {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, dict)
        }
    if not mapping:
        raise _config_error("JSON 中没有找到任何 MCP 服务配置")

    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for name, raw in mapping.items():
        if not isinstance(raw, dict):
            raise _config_error(f"服务 {name!r} 的配置必须是对象")
        server_id = normalize_server_id(name)
        base = server_id
        suffix = 2
        while server_id in seen:
            server_id = f"{base}-{suffix}"[:64]
            suffix += 1
        seen.add(server_id)
        entries.append((server_id, raw))
    return entries


def normalize_vendor_entry(server_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """把厂商配置裁剪成 Harness 能识别的连接参数。

    只保留 command/args/env/cwd/url/headers 等连接字段；
    工具、提示词、资源由 MCP 服务自己上报，不需要用户手填。
    """
    if not isinstance(raw, dict):
        raise _config_error("服务配置必须是对象", server_id=server_id)

    command = raw.get("command")
    url = next(
        (str(raw[key]) for key in VENDOR_URL_KEYS if isinstance(raw.get(key), str)),
        "",
    )
    transport_raw = raw.get("transport") or raw.get("type")
    if transport_raw:
        transport = _normalize_transport(transport_raw)
    elif command:
        transport = Transport.STDIO
    elif url:
        transport = Transport.STREAMABLE_HTTP
    else:
        raise _config_error(
            "服务配置缺少 command（stdio）或 url（网络传输）", server_id=server_id
        )

    enabled_value = raw.get("enabled")
    enabled = (
        bool(enabled_value)
        if isinstance(enabled_value, bool)
        else not bool(raw.get("disabled", False))
    )

    payload: dict[str, Any] = {
        "transport": (
            "streamable_http"
            if transport is Transport.STREAMABLE_HTTP
            else transport.value
        ),
        "enabled": enabled,
        "namespace": server_id,
    }
    if isinstance(raw.get("description"), str) and raw["description"].strip():
        payload["description"] = raw["description"].strip()[:500]

    if transport is Transport.STDIO:
        if not isinstance(command, str) or not command.strip():
            raise _config_error("stdio 服务缺少 command", server_id=server_id)
        payload["command"] = command.strip()
        args = raw.get("args", [])
        if not isinstance(args, list):
            raise _config_error("stdio 服务的 args 必须是数组", server_id=server_id)
        payload["args"] = [str(item) for item in args]
        env = raw.get("env")
        if isinstance(env, dict):
            payload["env"] = {str(key): str(value) for key, value in env.items()}
        if isinstance(raw.get("cwd"), str) and raw["cwd"].strip():
            payload["cwd"] = raw["cwd"].strip()
    else:
        if not url.startswith(("http://", "https://")):
            raise _config_error(
                f"{transport.value} 服务缺少合法的 http(s) url",
                server_id=server_id,
            )
        payload["url"] = url
        headers = raw.get("headers")
        if isinstance(headers, dict):
            payload["headers"] = {
                str(key): str(value) for key, value in headers.items()
            }
    return payload


def verify_parsed_config(raw: dict[str, Any]) -> None:
    """在写盘前做一次完整校验，避免把坏配置落盘。"""
    version = _as_int(raw.get("version"), 2, minimum=1)
    if version not in {1, 2}:
        raise _config_error("MCP 配置 version 只能是 1 或 2")
    defaults = raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {}
    merged_defaults = _merge_dicts(DEFAULT_DOC["defaults"], defaults)
    servers_raw = raw.get("servers", {})
    if not isinstance(servers_raw, dict):
        raise _config_error("servers 必须是对象")
    for server_id, server_raw in servers_raw.items():
        _build_server(str(server_id), server_raw, merged_defaults)



def _build_connection(
    server_id: str,
    raw: dict[str, Any],
    transport: Transport,
) -> dict[str, Any]:
    """从原始配置提取传输层参数，剔除调度字段。"""
    connection = {
        key: value
        for key, value in raw.items()
        if key not in LOCAL_CONFIG_KEYS and key != "transport"
    }
    connection["transport"] = (
        "streamable_http"
        if transport is Transport.STREAMABLE_HTTP
        else transport.value
    )
    if transport is Transport.STDIO:
        command = connection.get("command")
        args = connection.get("args", [])
        if not isinstance(command, str) or not command.strip():
            raise _config_error("stdio 服务缺少 command", server_id=server_id)
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise _config_error("stdio 服务的 args 必须是字符串列表", server_id=server_id)
        connection["args"] = list(args)
        if "env" in connection:
            connection["env"] = _expand_mapping(connection["env"])
        if "cwd" in connection and connection["cwd"] is not None:
            if not isinstance(connection["cwd"], str):
                raise _config_error("stdio 服务的 cwd 必须是字符串", server_id=server_id)
        _inject_npm_cache(server_id, connection)
    else:
        url = connection.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise _config_error(
                f"{transport.value} 服务缺少合法的 http(s) url",
                server_id=server_id,
            )
        if "headers" in connection:
            connection["headers"] = _expand_mapping(connection["headers"])
    return connection


def _build_server(
    server_id: str,
    raw: Any,
    defaults: dict[str, Any],
) -> ServerConfig:
    if not SERVER_ID_PATTERN.fullmatch(server_id):
        raise _config_error(f"非法服务名: {server_id}", server_id=server_id)
    if not isinstance(raw, dict):
        raise _config_error("服务配置必须是对象", server_id=server_id)

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _config_error("enabled 必须是布尔值", server_id=server_id)
    preconnect = raw.get("preconnect", False)
    if not isinstance(preconnect, bool):
        raise _config_error("preconnect 必须是布尔值", server_id=server_id)
    optional_legacy = bool(raw.get("optional", False))
    criticality = str(
        raw.get("criticality", "optional" if optional_legacy else "optional")
    ).strip().lower()
    if criticality not in {"required", "optional"}:
        raise _config_error("criticality 只能是 required / optional", server_id=server_id)

    namespace = str(raw.get("namespace") or server_id).strip()
    if not NAMESPACE_PATTERN.fullmatch(namespace):
        raise _config_error(
            "namespace 只能包含字母、数字、连字符和下划线，长度 1~32",
            server_id=server_id,
        )

    allow_tools = raw.get("allow_tools", defaults.get("allow_tools", []))
    deny_tools = raw.get("deny_tools", defaults.get("deny_tools", []))
    if not isinstance(allow_tools, list) or not all(
        isinstance(item, str) for item in allow_tools
    ):
        raise _config_error("allow_tools 必须是字符串列表", server_id=server_id)
    if not isinstance(deny_tools, list) or not all(
        isinstance(item, str) for item in deny_tools
    ):
        raise _config_error("deny_tools 必须是字符串列表", server_id=server_id)

    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise _config_error("tags 必须是字符串列表", server_id=server_id)

    transport_value = raw.get("transport", "stdio")
    if not enabled and not transport_value:
        transport_value = "stdio"
    transport = _normalize_transport(transport_value)

    namespace_policy_raw = defaults.get("namespace", {})
    if not isinstance(namespace_policy_raw, dict):
        namespace_policy_raw = {}
    namespace_policy = NamespacePolicy(
        mode=str(namespace_policy_raw.get("mode", "prefix")).lower(),
        separator=str(namespace_policy_raw.get("separator", "__")),
    )
    if namespace_policy.mode not in {"legacy", "prefix"}:
        raise _config_error("namespace.mode 只能是 legacy / prefix")
    if not namespace_policy.separator or any(
        char not in {"_", "-"} for char in namespace_policy.separator
    ):
        raise _config_error("namespace.separator 只能由 _ 或 - 组成")

    connection: dict[str, Any] = {}
    if enabled:
        connection = _build_connection(server_id, raw, transport)

    retry_raw = _merge_dicts(
        defaults.get("retry") or {},
        raw.get("retry") or {},
    )
    health_raw = _merge_dicts(
        defaults.get("health_check") or {},
        raw.get("health_check") or {},
    )
    connect_timeout = _as_int(
        raw.get("connect_timeout_ms", defaults.get("connect_timeout_ms", 15000)),
        15000,
        minimum=1000,
    )
    call_timeout = _as_int(
        raw.get("call_timeout_ms", defaults.get("call_timeout_ms", 120000)),
        120000,
        minimum=1000,
    )

    return ServerConfig(
        server_id=server_id,
        transport=transport,
        connection=connection,
        description=str(raw.get("description", "")),
        enabled=enabled,
        preconnect=preconnect,
        criticality=criticality,
        namespace=namespace,
        allow_tools=list(allow_tools),
        deny_tools=list(deny_tools),
        connect_timeout_ms=connect_timeout,
        call_timeout_ms=call_timeout,
        retry=_parse_retry(retry_raw),
        health=_parse_health(health_raw),
        tags=list(tags),
        namespace_mode=namespace_policy.mode,
        namespace_separator=namespace_policy.separator,
    )


class McpConfigStore:
    """负责 MCP 配置的读取、校验与原子写入。"""

    def __init__(
        self,
        path: str | Path,
        *,
        legacy_paths: list[str | Path] | None = None,
    ) -> None:
        self.path = Path(path)
        self.legacy_paths = [Path(item) for item in (legacy_paths or [])]
        self._cache: McpConfig | None = None
        self._defaults: PoolPolicy = PoolPolicy()

    @property
    def defaults(self) -> PoolPolicy:
        return self._defaults

    def load(self) -> McpConfig:
        """读取 JSON 配置文件；不存在时尝试迁移旧 YAML 或返回空配置。"""
        if not self.path.exists():
            migrated = self._migrate_legacy()
            if migrated is None:
                logger.info("MCP 配置文件不存在，使用空配置: %s", self.path)
                self._cache = self._parse({})
                return self._cache
            raw = migrated
        else:
            raw = self._read_json()

        self._cache = self._parse(raw)
        return self._cache

    def _migrate_legacy(self) -> dict[str, Any] | None:
        """把旧版 YAML 配置一次性迁移到统一的 JSON 文件。"""
        for legacy in self.legacy_paths:
            if not legacy.is_file():
                continue
            try:
                raw = yaml.safe_load(legacy.read_text(encoding="utf-8-sig")) or {}
            except yaml.YAMLError as error:
                raise _config_error(f"旧 MCP 配置 YAML 解析失败: {error}") from error
            if not isinstance(raw, dict):
                raise _config_error("旧 MCP 配置必须是 YAML 对象")
            raw.setdefault("version", 2)
            verify_parsed_config(raw)
            self._write(raw)
            logger.info("已将旧 MCP 配置迁移为 JSON：%s -> %s", legacy, self.path)
            return raw
        return None

    def _read_json(self) -> dict[str, Any]:
        raw_text = self.path.read_text(encoding="utf-8-sig")
        try:
            raw = json.loads(raw_text) if raw_text.strip() else {}
        except json.JSONDecodeError as error:
            raise _config_error(f"MCP 配置 JSON 解析失败: {error}") from error
        if not isinstance(raw, dict):
            raise _config_error("MCP 配置必须是 JSON 对象")
        return raw

    def current(self) -> McpConfig:
        return self._cache or self.load()


    def _parse(self, raw: dict[str, Any]) -> McpConfig:
        version = _as_int(raw.get("version"), 1, minimum=1)
        if version not in {1, 2}:
            raise _config_error("MCP 配置 version 只能是 1 或 2")

        defaults = {}
        if version >= 2:
            defaults = raw.get("defaults", {}) or {}
            if not isinstance(defaults, dict):
                raise _config_error("defaults 必须是对象")
        merged_defaults = _merge_dicts(DEFAULT_DOC["defaults"], defaults)
        pool_raw = merged_defaults.get("pool", {})
        if isinstance(pool_raw, dict):
            self._defaults = PoolPolicy(
                idle_ttl_ms=_as_int(pool_raw.get("idle_ttl_ms"), 300000, minimum=1000),
                max_pool_size=_as_int(pool_raw.get("max_pool_size"), 32, minimum=1),
                connect_singleflight=bool(
                    pool_raw.get("connect_singleflight", True)
                ),
                max_concurrent_per_key=_as_int(
                    pool_raw.get("max_concurrent_per_key"), 4, minimum=1
                ),
            )

        raw_servers = raw.get("servers", {})
        if not isinstance(raw_servers, dict):
            raise _config_error("servers 必须是对象")
        servers = {
            str(name): _build_server(str(name), config, merged_defaults)
            for name, config in raw_servers.items()
        }

        main_servers = raw.get("main_servers", [])
        if not isinstance(main_servers, list) or not all(
            isinstance(name, str) and name for name in main_servers
        ):
            raise _config_error("main_servers 必须是字符串列表")
        unknown = set(main_servers) - set(servers)
        if unknown:
            raise _config_error(f"main_servers 引用了未定义的服务: {sorted(unknown)}")

        fingerprint = ""
        try:
            fingerprint = self.path.read_text(encoding="utf-8-sig")
        except OSError:
            fingerprint = str(raw)

        return McpConfig(
            version=version,
            servers=servers,
            main_servers=list(dict.fromkeys(main_servers)),
            raw=raw,
            fingerprint=fingerprint,
        )

    # ---------- 写入 ----------

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_DOC)
        raw = self._read_json()
        return raw

    @staticmethod
    def _preserve_masked(
        incoming: Any,
        existing: Any,
    ) -> Any:
        """前端回传 ``***`` 时保留原密钥值。"""
        if isinstance(incoming, dict):
            result = dict(incoming)
            existing_dict = existing if isinstance(existing, dict) else {}
            for key, value in incoming.items():
                if value == "***" and key in existing_dict:
                    result[key] = existing_dict[key]
                elif isinstance(value, dict):
                    result[key] = McpConfigStore._preserve_masked(
                        value, existing_dict.get(key)
                    )
            return result
        return incoming

    def upsert_server(self, server_id: str, payload: dict[str, Any]) -> McpConfig:
        """新增或完整更新一个服务。"""
        if not SERVER_ID_PATTERN.fullmatch(server_id):
            raise _config_error(f"非法服务名: {server_id}", server_id=server_id)

        raw = self._read_raw()
        raw["version"] = 2
        raw.setdefault("servers", {})
        if not isinstance(raw["servers"], dict):
            raise _config_error("servers 必须是对象")

        existing = raw["servers"].get(server_id, {})
        if not isinstance(existing, dict):
            existing = {}
        merged = self._preserve_masked(dict(payload), existing)
        merged["transport"] = str(payload.get("transport", existing.get("transport", "stdio")))

        raw["servers"][server_id] = merged
        # 先完整校验，再写盘
        self._validate_raw(raw)
        self._write(raw)
        self._cache = self._parse(raw)
        return self._cache

    def delete_server(self, server_id: str) -> McpConfig:
        raw = self._read_raw()
        servers = raw.get("servers", {})
        if not isinstance(servers, dict) or server_id not in servers:
            raise McpError(
                f"服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                http_status=404,
            )
        servers.pop(server_id)
        main_servers = raw.get("main_servers", [])
        if isinstance(main_servers, list):
            raw["main_servers"] = [
                name for name in main_servers if name != server_id
            ]
        raw["version"] = 2
        self._validate_raw(raw)
        self._write(raw)
        self._cache = self._parse(raw)
        return self._cache

    def set_enabled(self, server_id: str, enabled: bool) -> McpConfig:
        raw = self._read_raw()
        servers = raw.get("servers", {})
        if not isinstance(servers, dict) or server_id not in servers:
            raise McpError(
                f"服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                http_status=404,
            )
        config = servers[server_id]
        if not isinstance(config, dict):
            raise _config_error("服务配置必须是对象", server_id=server_id)
        config["enabled"] = bool(enabled)
        raw["version"] = 2
        self._validate_raw(raw)
        self._write(raw)
        self._cache = self._parse(raw)
        return self._cache

    def import_servers(self, entries: list[tuple[str, dict[str, Any]]]) -> McpConfig:
        """一次性导入多个厂商配置，全部写回同一个 JSON 文件。"""
        raw = self._read_raw()
        raw["version"] = 2
        servers = raw.setdefault("servers", {})
        if not isinstance(servers, dict):
            raise _config_error("servers 必须是对象")
        for server_id, payload in entries:
            if not SERVER_ID_PATTERN.fullmatch(server_id):
                raise _config_error(f"非法服务名: {server_id}", server_id=server_id)
            existing = servers.get(server_id)
            # 编辑回填时前端可能带回 ***，这里保留原密钥值。
            servers[server_id] = self._preserve_masked(dict(payload), existing)

        # 粘贴导入即视为要接入主 Agent，避免用户还要手工维护 main_servers。
        main_servers = raw.get("main_servers", [])
        if not isinstance(main_servers, list):
            main_servers = []
        for server_id, payload in entries:
            if payload.get("enabled", True) and server_id not in main_servers:
                main_servers.append(server_id)
        raw["main_servers"] = main_servers
        verify_parsed_config(raw)
        self._write(raw)
        self._cache = self._parse(raw)
        return self._cache

    def _validate_raw(self, raw: dict[str, Any]) -> None:
        defaults = raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {}
        merged_defaults = _merge_dicts(DEFAULT_DOC["defaults"], defaults)
        servers_raw = raw.get("servers", {})
        if not isinstance(servers_raw, dict):
            raise _config_error("servers 必须是对象")
        for server_id, server_raw in servers_raw.items():
            _build_server(str(server_id), server_raw, merged_defaults)
        main_servers = raw.get("main_servers", [])
        if not isinstance(main_servers, list) or not all(
            isinstance(name, str) and name for name in main_servers
        ):
            raise _config_error("main_servers 必须是字符串列表")
        unknown = set(main_servers) - set(servers_raw)
        if unknown:
            raise _config_error(f"main_servers 引用了未定义的服务: {sorted(unknown)}")

    def _write(self, raw: dict[str, Any]) -> None:
        """先写临时文件再原子替换；保留一份 .bak。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".servers.",
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
        logger.info("MCP 配置已写入: %s", self.path)
