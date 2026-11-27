"""Plugin Protocol：JSON-RPC 2.0 信封、方法与错误码。

总线与插件只共享这一份协议定义；stdio / http / sandbox 三种传输共用同一套
方法名、错误码与超时语义，插件作者不需要感知传输差异。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_MAJOR = 1
SUPPORTED_PROTOCOL_MINOR = 0

# 总线 → 插件
METHOD_INITIALIZE = "plugin.initialize"
METHOD_DESCRIBE = "plugin.describe"
METHOD_HEALTH = "plugin.health"
METHOD_SHUTDOWN = "plugin.shutdown"
METHOD_TOOL_CALL = "tool.call"
METHOD_RESOURCE_LIST = "resource.list"
METHOD_RESOURCE_READ = "resource.read"
METHOD_PROMPT_GET = "prompt.get"
METHOD_HOOK_INVOKE = "hook.invoke"
METHOD_EVENT_NOTIFY = "event.notify"

# 插件 → 总线（白名单）
HOST_STORAGE_GET = "host.storage.get"
HOST_STORAGE_SET = "host.storage.set"
HOST_STORAGE_LIST = "host.storage.list"
HOST_STORAGE_DELETE = "host.storage.delete"
HOST_SECRET_GET = "host.secret.get"
HOST_LOG = "host.log"
HOST_PROGRESS = "host.progress"

BUS_METHODS = frozenset(
    {
        METHOD_INITIALIZE,
        METHOD_DESCRIBE,
        METHOD_HEALTH,
        METHOD_SHUTDOWN,
        METHOD_TOOL_CALL,
        METHOD_RESOURCE_LIST,
        METHOD_RESOURCE_READ,
        METHOD_PROMPT_GET,
        METHOD_HOOK_INVOKE,
        METHOD_EVENT_NOTIFY,
    }
)

HOST_METHODS = frozenset(
    {
        HOST_STORAGE_GET,
        HOST_STORAGE_SET,
        HOST_STORAGE_LIST,
        HOST_STORAGE_DELETE,
        HOST_SECRET_GET,
        HOST_LOG,
        HOST_PROGRESS,
    }
)

# 方法 → 默认超时（秒）；hook 失败只降级，不阻断主流程。
DEFAULT_TIMEOUTS = {
    METHOD_INITIALIZE: 15.0,
    METHOD_DESCRIBE: 15.0,
    METHOD_HEALTH: 5.0,
    METHOD_SHUTDOWN: 5.0,
    METHOD_TOOL_CALL: 60.0,
    METHOD_RESOURCE_LIST: 15.0,
    METHOD_RESOURCE_READ: 30.0,
    METHOD_PROMPT_GET: 10.0,
    METHOD_HOOK_INVOKE: 3.0,
    METHOD_EVENT_NOTIFY: 5.0,
}

# JSON-RPC 标准码 + 插件业务码。
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

ERROR_NOT_FOUND = -32001
ERROR_NOT_READY = -32002
ERROR_PERMISSION_DENIED = -32003
ERROR_TIMEOUT = -32004
ERROR_CAPABILITY_MISMATCH = -32005
ERROR_PROTOCOL_VERSION_MISMATCH = -32006
ERROR_CRASHED = -32007
ERROR_UNHEALTHY = -32008
ERROR_PACKAGE_INVALID = -32009
ERROR_TOOL_FAILED = -32010

ERROR_NAMES = {
    PARSE_ERROR: "PLUGIN_PARSE_ERROR",
    INVALID_REQUEST: "PLUGIN_INVALID_REQUEST",
    METHOD_NOT_FOUND: "PLUGIN_METHOD_NOT_FOUND",
    INVALID_PARAMS: "PLUGIN_INVALID_PARAMS",
    INTERNAL_ERROR: "PLUGIN_INTERNAL_ERROR",
    ERROR_NOT_FOUND: "PLUGIN_NOT_FOUND",
    ERROR_NOT_READY: "PLUGIN_NOT_READY",
    ERROR_PERMISSION_DENIED: "PLUGIN_PERMISSION_DENIED",
    ERROR_TIMEOUT: "PLUGIN_TIMEOUT",
    ERROR_CAPABILITY_MISMATCH: "PLUGIN_CAPABILITY_MISMATCH",
    ERROR_PROTOCOL_VERSION_MISMATCH: "PLUGIN_PROTOCOL_VERSION_MISMATCH",
    ERROR_CRASHED: "PLUGIN_CRASHED",
    ERROR_UNHEALTHY: "PLUGIN_UNHEALTHY",
    ERROR_PACKAGE_INVALID: "PLUGIN_PACKAGE_INVALID",
    ERROR_TOOL_FAILED: "PLUGIN_TOOL_FAILED",
}


def new_request_id() -> str:
    return uuid.uuid4().hex


def request(method: str, params: dict[str, Any] | None = None, *, request_id: str | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id or new_request_id(),
        "method": method,
        "params": params or {},
    }


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}


def success_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": payload}


def dumps(message: dict[str, Any]) -> str:
    """单行 JSON；stdio 传输一行一条消息。"""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def parse_message(raw: str | bytes) -> dict[str, Any]:
    """解析一行 JSON；失败抛 ``ValueError``，由传输层转换成错误响应。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON-RPC 消息必须是对象")
    return value


def message_kind(message: dict[str, Any]) -> str:
    """区分响应、请求、通知三种形态。"""
    if "method" in message:
        return "request" if "id" in message else "notification"
    if "id" in message and ("result" in message or "error" in message):
        return "response"
    return "invalid"


def validate_request(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """校验插件侧发来的请求，返回 (method, params)。"""
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("缺少 method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    return method, params


def protocol_compatible(version: str) -> bool:
    """主版本必须一致；次版本只允许不高于总线支持版本。"""
    try:
        major_text, minor_text = str(version).split(".", 1)
        major = int(major_text)
        minor = int(minor_text)
    except (TypeError, ValueError):
        return False
    if major != SUPPORTED_PROTOCOL_MAJOR:
        return False
    return minor <= SUPPORTED_PROTOCOL_MINOR


__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_MAJOR",
    "BUS_METHODS",
    "HOST_METHODS",
    "DEFAULT_TIMEOUTS",
    "METHOD_INITIALIZE",
    "METHOD_DESCRIBE",
    "METHOD_HEALTH",
    "METHOD_SHUTDOWN",
    "METHOD_TOOL_CALL",
    "METHOD_RESOURCE_LIST",
    "METHOD_RESOURCE_READ",
    "METHOD_PROMPT_GET",
    "METHOD_HOOK_INVOKE",
    "METHOD_EVENT_NOTIFY",
    "request",
    "notification",
    "success_response",
    "error_response",
    "dumps",
    "parse_message",
    "message_kind",
    "validate_request",
    "protocol_compatible",
]
