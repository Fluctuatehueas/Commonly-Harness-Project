"""远程插件 HTTP 传输。

每个请求是一次 POST + JSON-RPC 2.0 信封；可选 SSE 留给后续版本，P0 只实现
请求/响应。远程插件无法反向调用宿主，``host.*`` 只在本地 stdio 连接可用。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugin_bus import protocol
from plugin_bus.errors import PluginError, PluginTimeout
from plugin_bus.transport.base import HostHandler, Transport

logger = logging.getLogger(__name__)


class HttpTransport(Transport):
    """远程插件连接。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str,
        url: str,
        headers: dict[str, str] | None = None,
        host_handler: HostHandler | None = None,
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            version=version,
            host_handler=host_handler,
            log_path=None,
        )
        self.url = url
        self.headers = dict(headers or {})
        self._client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._client is not None:
            return
        if not self.url.startswith(("http://", "https://")):
            raise PluginError(
                f"远程插件地址非法: {self.url}",
                code="PLUGIN_MANIFEST_INVALID",
                plugin_id=self.plugin_id,
            )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"Content-Type": "application/json", **self.headers},
        )

    async def _post(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self._client is None:
            raise PluginError(
                "远程插件未连接", code="PLUGIN_NOT_READY", plugin_id=self.plugin_id
            )
        try:
            response = await self._client.post(
                self.url, json=message, timeout=timeout
            )
        except httpx.TimeoutException as error:
            raise PluginTimeout(
                f"远程插件请求超时: {message.get('method')}",
                plugin_id=self.plugin_id,
            ) from error
        except httpx.HTTPError as error:
            raise PluginError(
                f"远程插件连接失败: {error}",
                code="PLUGIN_CRASHED",
                plugin_id=self.plugin_id,
            ) from error
        if response.status_code >= 400:
            raise PluginError(
                f"远程插件返回 HTTP {response.status_code}",
                code="PLUGIN_TOOL_FAILED",
                plugin_id=self.plugin_id,
                detail={"status": response.status_code},
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise PluginError(
                "远程插件返回的不是合法 JSON",
                code="PLUGIN_TOOL_FAILED",
                plugin_id=self.plugin_id,
            ) from error
        if not isinstance(payload, dict):
            raise PluginError(
                "远程插件返回结构不合法",
                code="PLUGIN_TOOL_FAILED",
                plugin_id=self.plugin_id,
            )
        return payload

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        payload = await self._post(protocol.request(method, params), timeout)
        if "error" in payload:
            error = payload.get("error") or {}
            raise PluginError(
                str(error.get("message") or "远程插件调用失败"),
                code=_code_name(error.get("code")),
                plugin_id=self.plugin_id,
                detail={"rpc_code": error.get("code")},
            )
        return payload.get("result")

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            await self._post(protocol.notification(method, params), timeout=10.0)
        except PluginError:
            logger.debug("远程插件通知失败: %s", method, exc_info=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None


def _code_name(code: Any) -> str:
    try:
        return protocol.ERROR_NAMES.get(int(code), "PLUGIN_TOOL_FAILED")
    except (TypeError, ValueError):
        return "PLUGIN_TOOL_FAILED"


__all__ = ["HttpTransport"]
