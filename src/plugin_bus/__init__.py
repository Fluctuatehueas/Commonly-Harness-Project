"""Plugin Bus：Harness 外设总线。

分层：
- ``manifest`` / ``protocol``：对外协议与描述；
- ``storage`` / ``store``：磁盘与 PostgreSQL 状态；
- ``transport``：stdio / http / sandbox 三种传输；
- ``lifecycle``：进程与连接生命周期；
- ``runtime``：按用户隔离的运行时门面；
- ``installer`` / ``jobs``：安装编排与异步任务。
"""

from plugin_bus.errors import (
    PluginConflict,
    PluginError,
    PluginManifestError,
    PluginNotFound,
    PluginNotReady,
    PluginPackageInvalid,
    PluginPermissionDenied,
    PluginTimeout,
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
