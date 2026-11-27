"""插件生命周期与进程管理。

状态机：installed → verified → enabled → running → unhealthy → disabled → uninstalled。
进程按 ``(user_id, plugin_id, version)`` 复用，空闲回收，崩溃退避重启。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from plugin_bus import protocol
from plugin_bus.errors import (
    PluginError,
    PluginNotReady,
    PluginPermissionDenied,
    PluginTimeout,
)
from plugin_bus.manifest import Manifest
from plugin_bus.permissions import HostBridge
from plugin_bus.registry import CapabilityRegistry, CapabilitySnapshot, build_snapshot
from plugin_bus.storage import (
    InstalledPlugin,
    data_dir,
    logs_dir,
    stdio_log_path,
)
from plugin_bus.store import PluginStore
from plugin_bus.transport.base import Transport
from plugin_bus.transport.http import HttpTransport
from plugin_bus.transport.inprocess import InProcessTransport
from plugin_bus.transport.sandbox_stdio import SandboxStdioTransport
from plugin_bus.transport.stdio import StdioTransport

logger = logging.getLogger(__name__)

SandboxProvider = Callable[[str], Awaitable[Any]]


@dataclass
class RuntimeState:
    installed: InstalledPlugin
    status: str = "enabled"
    transport: Transport | None = None
    snapshot: CapabilitySnapshot | None = None
    last_used: float = 0.0
    failures: int = 0
    retry_at: float = 0.0
    error: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def manifest(self) -> Manifest:
        return self.installed.manifest

    @property
    def plugin_id(self) -> str:
        return self.installed.plugin_id

    @property
    def version(self) -> str:
        return self.installed.version


class PluginLifecycleManager:
    """单个用户的插件进程/连接管理。"""

    def __init__(
        self,
        *,
        user_id: str,
        store: PluginStore | None,
        registry: CapabilityRegistry,
        reserved_names: set[str] | None = None,
        sandbox_provider: SandboxProvider | None = None,
        inprocess_targets: dict[str, Any] | None = None,
        max_processes: int = 8,
        idle_ttl_seconds: int = 300,
        start_timeout: int = 15,
        call_timeout: int = 60,
        auto_disable_after: int = 5,
        progress_sink: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.user_id = user_id
        self.store = store
        self.registry = registry
        self.reserved_names = reserved_names or set()
        self.sandbox_provider = sandbox_provider
        self.inprocess_targets = dict(inprocess_targets or {})
        self.idle_ttl_seconds = idle_ttl_seconds
        self.start_timeout = start_timeout
        self.call_timeout = call_timeout
        self.auto_disable_after = auto_disable_after
        self.progress_sink = progress_sink
        self._states: dict[str, RuntimeState] = {}
        self._semaphore = asyncio.Semaphore(max_processes)
        self._restart_tasks: dict[str, asyncio.Task[Any]] = {}
        self._health_task: asyncio.Task[Any] | None = None
        self._closed = False

    # ---------- 候选与状态 ----------

    def set_installed(self, installed: dict[str, InstalledPlugin]) -> None:
        for plugin_id, item in installed.items():
            state = self._states.get(plugin_id)
            if state is None:
                self._states[plugin_id] = RuntimeState(installed=item)
            else:
                state.installed = item

    def state(self, plugin_id: str) -> RuntimeState | None:
        return self._states.get(plugin_id)

    def states(self) -> dict[str, RuntimeState]:
        return dict(self._states)

    def register_inprocess(self, plugin_id: str, target: Any) -> None:
        self.inprocess_targets[plugin_id] = target

    async def start_health_loop(self) -> None:
        if self._health_task is None:
            self._health_task = asyncio.create_task(
                self._health_loop(), name=f"plugin-health-{self.user_id}"
            )

    # ---------- 生命周期 ----------

    async def start(self, plugin_id: str, *, force: bool = False) -> CapabilitySnapshot:
        state = self._states.get(plugin_id)
        if state is None:
            raise PluginNotReady(f"插件未安装: {plugin_id}", plugin_id=plugin_id)
        if state.status == "disabled" and not force:
            raise PluginNotReady(f"插件已停用: {plugin_id}", plugin_id=plugin_id)
        if state.status in {"unhealthy"} and time.monotonic() < state.retry_at and not force:
            raise PluginNotReady(
                f"插件正在退避重启: {plugin_id}", plugin_id=plugin_id
            )
        async with state.lock:
            if state.transport is not None and state.status == "running":
                if state.snapshot is not None:
                    return state.snapshot
            await self._start_locked(state)
            assert state.snapshot is not None
            return state.snapshot

    async def _start_locked(self, state: RuntimeState) -> None:
        await self._close_transport(state)
        manifest = state.manifest
        state.status = "starting"
        state.error = ""
        try:
            transport = await self._build_transport(state)
            await transport.open()
            init_params = {
                "protocolVersion": protocol.PROTOCOL_VERSION,
                "plugin_id": manifest.plugin_id,
                "version": manifest.version,
                "user_id": self.user_id,
                "permissions": list(manifest.permissions),
                "config": await self._load_config(manifest.plugin_id),
            }
            await transport.request(
                protocol.METHOD_INITIALIZE,
                init_params,
                timeout=float(self.start_timeout),
            )
            describe = await transport.request(
                protocol.METHOD_DESCRIBE, {}, timeout=float(self.start_timeout)
            )
            if not isinstance(describe, dict):
                raise PluginError(
                    "plugin.describe 返回值必须是对象",
                    code="PLUGIN_CAPABILITY_MISMATCH",
                    http_status=422,
                    plugin_id=manifest.plugin_id,
                )
            snapshot = build_snapshot(manifest, describe)
            self.registry.register(
                self.user_id, snapshot, reserved_names=self.reserved_names
            )
            self.registry.save(self.user_id)
            state.transport = transport
            state.snapshot = snapshot
            state.status = "running"
            state.failures = 0
            state.retry_at = 0.0
            state.last_used = time.monotonic()
            await self._set_status(manifest, "running")
            logger.info(
                "插件已启动: user=%s plugin=%s version=%s tools=%d",
                self.user_id,
                manifest.plugin_id,
                manifest.version,
                len(snapshot.tools),
            )
        except Exception as error:
            state.status = "unhealthy"
            state.error = str(error)
            state.failures += 1
            await self._close_transport(state)
            await self._record_failure(state, error)
            raise

    async def stop(self, plugin_id: str, *, status: str = "enabled") -> None:
        state = self._states.get(plugin_id)
        if state is None:
            return
        async with state.lock:
            await self._close_transport(state)
            state.status = status
            await self._set_status(state.manifest, status)

    async def restart(self, plugin_id: str) -> CapabilitySnapshot:
        state = self._states.get(plugin_id)
        if state is None:
            raise PluginNotReady(f"插件未安装: {plugin_id}", plugin_id=plugin_id)
        await self.stop(plugin_id, status="enabled")
        return await self.start(plugin_id, force=True)

    async def mark_disabled(self, plugin_id: str, *, auto: bool = False) -> None:
        await self.stop(plugin_id, status="disabled")
        state = self._states.get(plugin_id)
        manifest = state.manifest if state else None
        if self.store and manifest is not None:
            await self.store.upsert_user_state(
                user_id=self.user_id,
                plugin_id=plugin_id,
                enabled=False,
                current_version=manifest.version,
                auto_disabled=auto,
            )
            await self.store.audit(
                user_id=self.user_id,
                plugin_id=plugin_id,
                version=manifest.version,
                action="disable",
                result="ok" if not auto else "auto_disabled",
                detail={"auto": auto},
            )

    # ---------- 调用 ----------

    async def ensure_running(self, plugin_id: str) -> RuntimeState:
        state = self._states.get(plugin_id)
        if state is None:
            raise PluginNotReady(f"插件未安装: {plugin_id}", plugin_id=plugin_id)
        if state.status != "running" or state.transport is None:
            await self.start(plugin_id)
        assert state.transport is not None
        state.last_used = time.monotonic()
        return state

    async def call_tool(
        self, plugin_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        async with self._semaphore:
            state = await self.ensure_running(plugin_id)
            snapshot = state.snapshot
            if snapshot is None or not any(
                tool.remote_name == tool_name for tool in snapshot.tools
            ):
                raise PluginPermissionDenied(
                    f"插件未声明工具: {tool_name}", plugin_id=plugin_id
                )
            if state.transport is None:
                raise PluginNotReady(f"插件未就绪: {plugin_id}", plugin_id=plugin_id)
            try:
                result = await state.transport.request(
                    protocol.METHOD_TOOL_CALL,
                    {"name": tool_name, "arguments": arguments},
                    timeout=float(self.call_timeout),
                )
            except Exception as error:
                await self._on_call_failure(state, error)
                raise
            state.last_used = time.monotonic()
            if isinstance(result, str):
                return result
            if isinstance(result, dict) and isinstance(result.get("content"), str):
                return result["content"]
            import json

            return json.dumps(result, ensure_ascii=False)

    async def invoke_hook(
        self, plugin_id: str, hook_name: str, payload: dict[str, Any], *, timeout: float = 3.0
    ) -> Any:
        """hook 超时或异常只降级，不阻断主流程。"""
        try:
            state = await self.ensure_running(plugin_id)
            if state.transport is None:
                return None
            return await state.transport.request(
                protocol.METHOD_HOOK_INVOKE,
                {"hook": hook_name, "payload": payload},
                timeout=timeout,
            )
        except Exception as error:
            logger.info("插件 hook 降级: %s %s (%s)", plugin_id, hook_name, error)
            return None

    async def list_resources(self, plugin_id: str) -> list[dict[str, Any]]:
        state = await self.ensure_running(plugin_id)
        if state.transport is None:
            return []
        result = await state.transport.request(
            protocol.METHOD_RESOURCE_LIST, {}, timeout=15.0
        )
        return result if isinstance(result, list) else []

    async def read_resource(self, plugin_id: str, uri: str) -> Any:
        state = await self.ensure_running(plugin_id)
        if state.transport is None:
            raise PluginNotReady(f"插件未就绪: {plugin_id}", plugin_id=plugin_id)
        return await state.transport.request(
            protocol.METHOD_RESOURCE_READ, {"uri": uri}, timeout=30.0
        )

    async def get_prompt(self, plugin_id: str, prompt_id: str) -> Any:
        state = await self.ensure_running(plugin_id)
        if state.transport is None:
            raise PluginNotReady(f"插件未就绪: {plugin_id}", plugin_id=plugin_id)
        return await state.transport.request(
            protocol.METHOD_PROMPT_GET, {"id": prompt_id}, timeout=10.0
        )

    async def health(self, plugin_id: str) -> dict[str, Any]:
        state = self._states.get(plugin_id)
        if state is None:
            return {"plugin_id": plugin_id, "state": "missing"}
        if state.transport is None:
            return {
                "plugin_id": plugin_id,
                "state": state.status,
                "error": state.error,
            }
        started = time.monotonic()
        try:
            await state.transport.request(
                protocol.METHOD_HEALTH, {}, timeout=5.0
            )
            latency = int((time.monotonic() - started) * 1000)
            return {
                "plugin_id": plugin_id,
                "state": "running",
                "latency_ms": latency,
                "error": "",
            }
        except Exception as error:
            return {
                "plugin_id": plugin_id,
                "state": state.status,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": str(error),
            }

    # ---------- 内部 ----------

    async def _build_transport(self, state: RuntimeState) -> Transport:
        manifest = state.manifest
        host_bridge = HostBridge(
            manifest=manifest,
            user_id=self.user_id,
            data_root=data_dir(self.user_id, manifest.plugin_id),
            logs_root=logs_dir(self.user_id, manifest.plugin_id),
            progress=self._progress_callback(manifest.plugin_id),
        )
        if manifest.plugin_id in self.inprocess_targets:
            return InProcessTransport(
                plugin_id=manifest.plugin_id,
                version=manifest.version,
                target=self.inprocess_targets[manifest.plugin_id],
                host_handler=host_bridge.handle,
            )
        if manifest.runtime.kind == "inprocess":
            raise PluginError(
                f"内置插件入口未注册: {manifest.plugin_id}",
                code="PLUGIN_NOT_READY",
                plugin_id=manifest.plugin_id,
            )
        if manifest.transport.kind == "http":
            config = await self._load_config(manifest.plugin_id)
            headers = config.get("headers") if isinstance(config, dict) else None
            return HttpTransport(
                plugin_id=manifest.plugin_id,
                version=manifest.version,
                url=manifest.transport.url,
                headers=headers if isinstance(headers, dict) else None,
                host_handler=None,
            )
        if manifest.transport.kind == "sandbox_stdio":
            if self.sandbox_provider is None:
                raise PluginError(
                    "项目级插件需要沙箱后端，但当前不可用",
                    code="PLUGIN_NOT_READY",
                    plugin_id=manifest.plugin_id,
                )
            backend = await self.sandbox_provider(self.user_id)
            remote_root = (
                f"/tmp/harness-plugins/{self.user_id}/"
                f"{manifest.plugin_id}/{manifest.version}"
            )
            return SandboxStdioTransport(
                plugin_id=manifest.plugin_id,
                version=manifest.version,
                package_root=state.installed.path,
                command=manifest.runtime.command,
                args=manifest.runtime.args,
                sandbox_backend=backend,
                remote_root=remote_root,
                host_handler=None,
            )
        if manifest.runtime.kind == "remote":
            raise PluginError(
                "远程插件必须使用 http 传输",
                code="PLUGIN_MANIFEST_INVALID",
                plugin_id=manifest.plugin_id,
            )
        return StdioTransport(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            cwd=state.installed.path,
            command=manifest.runtime.command,
            args=manifest.runtime.args,
            host_handler=host_bridge.handle,
            log_path=stdio_log_path(self.user_id, manifest.plugin_id),
        )

    def _progress_callback(self, plugin_id: str) -> Callable[[dict[str, Any]], Any] | None:
        if self.progress_sink is None:
            return None

        def emit(payload: dict[str, Any]) -> Any:
            return self.progress_sink(plugin_id, payload)

        return emit

    async def _load_config(self, plugin_id: str) -> dict[str, Any]:
        if self.store is None:
            return {}
        try:
            return await self.store.get_config(self.user_id, plugin_id)
        except Exception:
            logger.debug("读取插件配置失败: %s", plugin_id, exc_info=True)
            return {}

    async def _set_status(self, manifest: Manifest, status: str) -> None:
        if self.store is None:
            return
        try:
            await self.store.set_install_status(
                self.user_id, manifest.plugin_id, manifest.version, status
            )
        except Exception:
            logger.debug("更新插件状态失败: %s", manifest.plugin_id, exc_info=True)

    async def _close_transport(self, state: RuntimeState) -> None:
        transport = state.transport
        state.transport = None
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                logger.debug("关闭插件传输失败: %s", state.plugin_id, exc_info=True)

    async def _record_failure(self, state: RuntimeState, error: Exception) -> None:
        state.error = str(error)
        state.retry_at = time.monotonic() + min(2 ** max(state.failures, 1), 60)
        if self.store is not None:
            await self.store.audit(
                user_id=self.user_id,
                plugin_id=state.plugin_id,
                version=state.version,
                action="failure",
                result="failed",
                detail={"error": str(error), "failures": state.failures},
            )
        if state.failures >= self.auto_disable_after:
            # 可能处于状态锁内，自动停用放到独立任务，避免重复获取同一把锁。
            asyncio.create_task(
                self._auto_disable(state.plugin_id),
                name=f"plugin-autodisable-{state.plugin_id}",
            )
            return
        self._schedule_restart(state)

    async def _auto_disable(self, plugin_id: str) -> None:
        try:
            await self.mark_disabled(plugin_id, auto=True)
        except Exception:
            logger.debug("插件自动停用失败: %s", plugin_id, exc_info=True)

    def _schedule_restart(self, state: RuntimeState) -> None:
        if state.plugin_id in self._restart_tasks:
            return
        delay = max(state.retry_at - time.monotonic(), 0.5)

        async def _retry() -> None:
            try:
                await asyncio.sleep(delay)
                if self._closed:
                    return
                await self.start(state.plugin_id, force=True)
            except Exception:
                logger.debug("插件自动重启失败: %s", state.plugin_id, exc_info=True)
            finally:
                self._restart_tasks.pop(state.plugin_id, None)
                # start 失败时 _record_failure 因任务仍在而不会再次排程，
                # 这里补上下一次退避，直到成功或触发自动停用。
                if (
                    not self._closed
                    and state.status == "unhealthy"
                    and state.failures < self.auto_disable_after
                ):
                    self._schedule_restart(state)

        self._restart_tasks[state.plugin_id] = asyncio.create_task(
            _retry(), name=f"plugin-restart-{state.plugin_id}"
        )

    async def _on_call_failure(self, state: RuntimeState, error: Exception) -> None:
        if isinstance(error, PluginPermissionDenied):
            return
        if isinstance(error, (PluginTimeout, PluginError)) and getattr(
            error, "code", ""
        ) in {"PLUGIN_CRASHED", "PLUGIN_NOT_READY", "PLUGIN_TIMEOUT"}:
            state.failures += 1
            state.status = "unhealthy"
            await self._close_transport(state)
            await self._record_failure(state, error)

    async def _health_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(30)
            for plugin_id, state in list(self._states.items()):
                if state.status != "running" or state.transport is None:
                    continue
                result = await self.health(plugin_id)
                if result.get("state") == "running":
                    state.failures = 0
                    continue
                state.failures += 1
                state.status = "unhealthy"
                await self._close_transport(state)
                await self._record_failure(
                    state, PluginError(str(result.get("error") or "健康检查失败"))
                )

    async def recycle_idle(self) -> None:
        """回收空闲超过 TTL 的进程；LKG 快照保留，下次调用自动拉起。"""
        now = time.monotonic()
        for plugin_id, state in list(self._states.items()):
            if state.status != "running" or state.transport is None:
                continue
            if now - state.last_used < self.idle_ttl_seconds:
                continue
            await self.stop(plugin_id, status="enabled")
            logger.info("插件空闲回收: %s", plugin_id)

    async def shutdown(self) -> None:
        self._closed = True
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None
        for task in list(self._restart_tasks.values()):
            task.cancel()
        self._restart_tasks.clear()
        for plugin_id in list(self._states):
            await self.stop(plugin_id, status="enabled")


__all__ = ["PluginLifecycleManager", "RuntimeState"]
