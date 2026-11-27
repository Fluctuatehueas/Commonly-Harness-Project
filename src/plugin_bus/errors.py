"""插件系统统一错误类型。

所有总线、协议、权限、生命周期错误共用 ``PluginError``，由 API 层转换成
HTTP 响应；插件进程内部异常不直接透出，统一转换为 ``PLUGIN_*`` 业务错误码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginError(Exception):
    """插件系统基础错误。

    Attributes:
        message: 面向用户的错误说明。
        code: 业务错误码（见 ``protocol.ERROR_*``）。
        http_status: API 层映射的 HTTP 状态码。
        plugin_id: 相关插件；与插件无关的配置错误可为空。
        detail: 附加信息，写审计与调试日志用。
    """

    message: str
    code: str = "PLUGIN_ERROR"
    http_status: int = 500
    plugin_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "plugin_id": self.plugin_id,
            **({"detail": self.detail} if self.detail else {}),
        }


class PluginManifestError(PluginError):
    """manifest 解析或校验失败。"""

    def __init__(self, message: str, *, plugin_id: str = "", detail: dict[str, Any] | None = None):
        super().__init__(
            message,
            code="PLUGIN_MANIFEST_INVALID",
            http_status=422,
            plugin_id=plugin_id,
            detail=detail or {},
        )


class PluginNotFound(PluginError):
    def __init__(self, plugin_id: str, *, message: str = ""):
        super().__init__(
            message or f"插件不存在: {plugin_id}",
            code="PLUGIN_NOT_FOUND",
            http_status=404,
            plugin_id=plugin_id,
        )


class PluginConflict(PluginError):
    def __init__(self, message: str, *, plugin_id: str = ""):
        super().__init__(
            message,
            code="PLUGIN_CONFLICT",
            http_status=409,
            plugin_id=plugin_id,
        )


class PluginPermissionDenied(PluginError):
    def __init__(self, message: str, *, plugin_id: str = "", detail: dict[str, Any] | None = None):
        super().__init__(
            message,
            code="PLUGIN_PERMISSION_DENIED",
            http_status=403,
            plugin_id=plugin_id,
            detail=detail or {},
        )


class PluginNotReady(PluginError):
    def __init__(self, message: str, *, plugin_id: str = ""):
        super().__init__(
            message,
            code="PLUGIN_NOT_READY",
            http_status=503,
            plugin_id=plugin_id,
        )


class PluginTimeout(PluginError):
    def __init__(self, message: str, *, plugin_id: str = ""):
        super().__init__(
            message,
            code="PLUGIN_TIMEOUT",
            http_status=504,
            plugin_id=plugin_id,
        )


class PluginPackageInvalid(PluginError):
    def __init__(self, message: str, *, plugin_id: str = "", detail: dict[str, Any] | None = None):
        super().__init__(
            message,
            code="PLUGIN_PACKAGE_INVALID",
            http_status=422,
            plugin_id=plugin_id,
            detail=detail or {},
        )


__all__ = [
    "PluginError",
    "PluginManifestError",
    "PluginNotFound",
    "PluginConflict",
    "PluginPermissionDenied",
    "PluginNotReady",
    "PluginTimeout",
    "PluginPackageInvalid",
]
