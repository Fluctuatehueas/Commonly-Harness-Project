"""项目级插件运行器（``.commonharness/plugins``）。

项目级插件不进用户目录、不落 PG、不出现在控制台插件页；只在项目会话中按需
发现，并强制在沙箱内以一次性进程执行。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from plugin_bus import protocol
from plugin_bus.errors import PluginError, PluginNotFound
from plugin_bus.manifest import Manifest
from plugin_bus.storage import InstalledPlugin, discover_project_plugins
from plugin_bus.transport.base import Transport
from plugin_bus.transport.sandbox_stdio import SandboxStdioTransport

logger = logging.getLogger(__name__)

SandboxProvider = Callable[[str], Awaitable[Any]]
TransportFactory = Callable[[InstalledPlugin, Any, str], Transport]


@dataclass(frozen=True)
class ProjectPluginInfo:
    plugin_id: str
    name: str
    version: str
    description: str
    path: Path
    manifest: Manifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": "project",
            "path": str(self.path),
        }


class ProjectPluginRunner:
    """按项目根目录发现并运行项目级插件。"""

    def __init__(
        self,
        *,
        user_id: str,
        project_root: Path,
        sandbox_provider: SandboxProvider,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.user_id = user_id
        self.project_root = Path(project_root).resolve()
        self.sandbox_provider = sandbox_provider
        self.transport_factory = transport_factory
        self._transports: dict[str, Transport] = {}
        self._installed: dict[str, InstalledPlugin] = discover_project_plugins(
            self.project_root
        )

    def list_plugins(self) -> list[ProjectPluginInfo]:
        return [
            ProjectPluginInfo(
                plugin_id=item.plugin_id,
                name=item.manifest.name,
                version=item.version,
                description=item.manifest.description,
                path=item.path,
                manifest=item.manifest,
            )
            for item in sorted(
                self._installed.values(), key=lambda entry: entry.manifest.name.casefold()
            )
        ]

    async def describe(self, plugin_id: str) -> dict[str, Any]:
        transport = await self._transport(plugin_id)
        init = self._initialize_params(self._installed[plugin_id].manifest)
        await transport.request(
            protocol.METHOD_INITIALIZE, init, timeout=15.0
        )
        result = await transport.request(protocol.METHOD_DESCRIBE, {}, timeout=15.0)
        return result if isinstance(result, dict) else {}

    async def call_tool(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> str:
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        if not installed.manifest.has_capability("tool"):
            raise PluginError(
                f"项目级插件未声明 tool 能力: {plugin_id}",
                code="PLUGIN_CAPABILITY_MISMATCH",
                plugin_id=plugin_id,
            )
        transport = await self._transport(plugin_id)
        await transport.request(
            protocol.METHOD_INITIALIZE,
            self._initialize_params(installed.manifest),
            timeout=15.0,
        )
        result = await transport.request(
            protocol.METHOD_TOOL_CALL,
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            return result["content"]
        return json.dumps(result, ensure_ascii=False)

    async def close(self) -> None:
        for transport in list(self._transports.values()):
            try:
                await transport.close()
            except Exception:
                logger.debug("关闭项目级插件传输失败", exc_info=True)
        self._transports.clear()

    def _initialize_params(self, manifest: Manifest) -> dict[str, Any]:
        return {
            "protocolVersion": protocol.PROTOCOL_VERSION,
            "plugin_id": manifest.plugin_id,
            "version": manifest.version,
            "user_id": self.user_id,
            "permissions": list(manifest.permissions),
            "config": {},
            "project_root": str(self.project_root),
        }

    async def _transport(self, plugin_id: str) -> Transport:
        existing = self._transports.get(plugin_id)
        if existing is not None:
            return existing
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        backend = await self.sandbox_provider(self.user_id)
        digest = hashlib.sha1(
            str(self.project_root).encode("utf-8")
        ).hexdigest()[:12]
        remote_root = (
            f"/tmp/harness-project-plugins/{self.user_id}/{digest}/"
            f"{plugin_id}/{installed.version}"
        )
        if self.transport_factory is not None:
            transport = self.transport_factory(installed, backend, remote_root)
        else:
            transport = SandboxStdioTransport(
                plugin_id=plugin_id,
                version=installed.version,
                package_root=installed.path,
                command=installed.manifest.runtime.command,
                args=installed.manifest.runtime.args,
                sandbox_backend=backend,
                remote_root=remote_root,
                host_handler=None,
            )
        await transport.open()
        self._transports[plugin_id] = transport
        return transport


__all__ = ["ProjectPluginRunner", "ProjectPluginInfo", "SandboxProvider", "TransportFactory"]
