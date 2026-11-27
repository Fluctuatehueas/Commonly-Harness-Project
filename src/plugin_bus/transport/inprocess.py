"""内置插件的进程内适配器。

内置插件与外部插件共用同一份 manifest 与协议语义，只是把传输换成进程内调用。
适配对象可以实现统一的 ``handle(method, params)``，也可以按协议方法拆成
``initialize`` / ``describe`` / ``tool_call`` 等方法。
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from typing import Any

from plugin_bus.errors import PluginError
from plugin_bus.transport.base import HostHandler, Transport

METHOD_ATTRS = {
    "plugin.initialize": "initialize",
    "plugin.describe": "describe",
    "plugin.health": "health",
    "plugin.shutdown": "shutdown",
    "tool.call": "tool_call",
    "resource.list": "resource_list",
    "resource.read": "resource_read",
    "prompt.get": "prompt_get",
    "hook.invoke": "hook_invoke",
    "event.notify": "event_notify",
}


def load_file_target(plugin_dir: str | Path, entry: str) -> Any:
    """按 ``module:attr`` 从内置插件目录加载进程内入口。

    支持目录名或 plugin_id 含连字符；不依赖 Python 包名，也不把内置插件
    复制到用户目录。
    """
    module_name, _, attr = str(entry or "").partition(":")
    module_name = module_name.strip()
    if not module_name:
        raise PluginError(
            "内置插件缺少 runtime.entry", code="PLUGIN_MANIFEST_INVALID"
        )
    module_file = Path(plugin_dir) / (
        module_name if module_name.endswith(".py") else f"{module_name}.py"
    )
    if not module_file.is_file():
        raise PluginError(
            f"内置插件入口不存在: {module_file}",
            code="PLUGIN_MANIFEST_INVALID",
        )
    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", f"harness_builtin_{plugin_dir}_{module_name}")
    spec = importlib.util.spec_from_file_location(safe_name, module_file)
    if spec is None or spec.loader is None:
        raise PluginError(
            f"无法加载内置插件入口: {module_file}",
            code="PLUGIN_MANIFEST_INVALID",
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not attr:
        return module
    target = getattr(module, attr, None)
    if target is None:
        raise PluginError(
            f"内置插件入口缺少属性: {entry}",
            code="PLUGIN_MANIFEST_INVALID",
        )
    return target


class InProcessTransport(Transport):
    """进程内适配器；不创建子进程，不持久化任何外部资源。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str,
        target: Any,
        host_handler: HostHandler | None = None,
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            version=version,
            host_handler=host_handler,
            log_path=None,
        )
        self.target = target
        self._closed = False

    async def open(self) -> None:
        setter = getattr(self.target, "set_host_handler", None)
        if callable(setter) and self.host_handler is not None:
            result = setter(self.host_handler)
            if inspect.isawaitable(result):
                await result

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        if self._closed:
            raise PluginError(
                "插件已关闭", code="PLUGIN_NOT_READY", plugin_id=self.plugin_id
            )
        if callable(self.target) and not hasattr(self.target, "handle"):
            # runtime.entry 指向一个 handle(method, params) 函数。
            result = self.target(method, params)
        else:
            handler = getattr(self.target, "handle", None)
            if callable(handler):
                result = handler(method, params)
            else:
                attr = METHOD_ATTRS.get(method)
                method_impl = getattr(self.target, attr, None) if attr else None
                if not callable(method_impl):
                    raise PluginError(
                        f"内置插件未实现方法: {method}",
                        code="PLUGIN_METHOD_NOT_FOUND",
                        plugin_id=self.plugin_id,
                    )
                # 统一传位置参数：initialize(params) / tool_call(params)。
                result = method_impl(params)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        try:
            await self.request(method, params, timeout=5.0)
        except PluginError:
            # 通知失败不影响主流程。
            return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if callable(self.target) and not hasattr(self.target, "shutdown"):
            result = self.target("plugin.shutdown", {"reason": "bus_close"})
            if inspect.isawaitable(result):
                await result
            return
        shutdown = getattr(self.target, "shutdown", None)
        if callable(shutdown):
            result = shutdown({"reason": "bus_close"})
            if inspect.isawaitable(result):
                await result

    @property
    def connected(self) -> bool:
        return not self._closed


__all__ = ["InProcessTransport", "METHOD_ATTRS", "load_file_target"]
