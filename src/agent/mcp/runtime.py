"""MCP 运行时门面：配置、懒连接、状态机、工具注册与调用路由。

启动阶段只加载配置与 LKG 缓存，不建立任何 MCP 连接。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import MCP_CONFIG_PATH, MCP_ROOT, PROJECT_ROOT
from agent.mcp.client_registry import MCPClientRegistry
from agent.mcp.config_store import McpConfig, McpConfigStore
from agent.mcp.observer import McpObserver
from agent.mcp.registry import ToolRegistry
from agent.mcp.router import CallRouter, classify_exception
from agent.mcp.types import (
    ErrorCategory,
    McpError,
    ServerConfig,
    ServerRuntime,
    ServerState,
    ToolMeta,
    utc_now,
)

logger = logging.getLogger(__name__)

MCP_CONFIG_ENV = "HARNESS_MCP_CONFIG_PATH"
DEFAULT_MCP_CONFIG_PATH = MCP_CONFIG_PATH
# 旧版 YAML 只在 JSON 不存在时做一次性迁移。
LEGACY_MCP_CONFIG_PATHS = [
    PROJECT_ROOT / "mcp_servers.yaml",
    MCP_ROOT / "servers.yaml",
]

# 内置工具名，MCP 工具不得占用。
RESERVED_TOOL_NAMES = {
    "read_word_document",
    "request_additional_info",
    "request_user_choice",
    "task",
    "start_async_task",
    "download_skill",
    "assign_skill",
    "list_subagent_skills",
    "delete_subagent_skill",
    "update_subagent_skill",
    "search_web",
    "fetch_web",
    "read_file",
    "write_file",
    "edit_file",
}

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_SECONDS = 60


class MCPRuntime:
    """MCP 模块的单一运行时入口。"""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        resolved = self._resolve_config_path(config_path)
        self.config_store = McpConfigStore(
            resolved,
            legacy_paths=LEGACY_MCP_CONFIG_PATHS,
        )
        self.registry = ToolRegistry()
        self.observer = McpObserver()
        self.pool = MCPClientRegistry(log_sink=self._record_subprocess_log)
        self.router = CallRouter(
            self.pool,
            self.registry,
            self.observer,
            config_provider=self.config_for,
            state_provider=self.state_for,
            failure_handler=self.on_tool_failure,
        )
        self._states: dict[str, ServerRuntime] = {}
        self._config: McpConfig = McpConfig(
            version=2, servers={}, main_servers=[], raw={}
        )
        self._config_error = ""
        self._initialized = False
        self._configured = False
        self._lock = asyncio.Lock()
        self._discover_locks: dict[str, asyncio.Lock] = {}
        self._retry_tasks: dict[str, asyncio.Task[Any]] = {}
        self._watch_task: asyncio.Task[Any] | None = None
        self._health_task: asyncio.Task[Any] | None = None
        self._structure_handler: Any = None
        self._structure_key: tuple[Any, ...] = ()

    @staticmethod
    def _resolve_config_path(path: str | Path | None) -> Path:
        if path is not None:
            return Path(path)
        configured = os.getenv(MCP_CONFIG_ENV)
        return Path(configured) if configured else DEFAULT_MCP_CONFIG_PATH

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """加载配置与 LKG 缓存；建立状态机；不连接任何服务。"""
        if self._initialized:
            return
        self._configured = True
        try:
            self._config = await asyncio.to_thread(self.config_store.load)
            self._config_error = ""
        except McpError as error:
            self._config_error = error.message
            self.observer.record(
                "mcp.config.write",
                level="error",
                message=error.message,
                config_error=True,
            )
            logger.error("MCP 配置加载失败，模块以降级状态启动: %s", error)
            self._config = McpConfig(version=2, servers={}, main_servers=[], raw={})
        except Exception as error:  # noqa: BLE001
            self._config_error = str(error)
            logger.exception("MCP 配置加载异常，模块以降级状态启动")
            self._config = McpConfig(version=2, servers={}, main_servers=[], raw={})

        self._rebuild_states(load_cache=True)
        self.pool.policy = self.config_store.defaults
        self._structure_key = self._compute_structure_key()
        self.pool.start()
        self._health_task = asyncio.create_task(
            self._health_loop(), name="mcp-health-loop"
        )
        self._initialized = True
        logger.info(
            "MCP 模块已初始化：%d 个服务，启动连接数=0",
            len(self._states),
        )
        # preconnect 是显式 opt-in，默认没有服务会命中。
        for server_id, state in self._states.items():
            if (
                self._config.servers[server_id].enabled
                and self._config.servers[server_id].preconnect
            ):
                asyncio.create_task(
                    self._discover(server_id, force=True, reason="preconnect"),
                    name=f"mcp-preconnect-{server_id}",
                )

    def _rebuild_states(self, *, load_cache: bool) -> None:
        configs = self._config.servers
        existing = self._states
        next_states: dict[str, ServerRuntime] = {}
        for server_id, config in configs.items():
            runtime = existing.get(server_id)
            if runtime is None:
                runtime = ServerRuntime(
                    config=config,
                    state=ServerState.DORMANT if config.enabled else ServerState.DISABLED,
                )
            else:
                runtime.config = config
                if not config.enabled:
                    runtime.state = ServerState.DISABLED
                elif runtime.state is ServerState.DISABLED:
                    runtime.state = ServerState.DORMANT
            next_states[server_id] = runtime
        self._states = next_states

        if load_cache:
            for server_id, config in configs.items():
                if not config.enabled:
                    continue
                metas = self.registry.cached_metas(server_id)
                if metas:
                    self._register_cached(config, metas)

    def _register_cached(self, config: ServerConfig, metas: list[ToolMeta]) -> None:
        changed = self.registry.register(
            config,
            metas,
            source="lkg_cache",
            reserved_names=RESERVED_TOOL_NAMES,
        )
        runtime = self._states.get(config.server_id)
        if runtime is not None:
            runtime.tool_count = self.registry.tool_count(config.server_id)
            runtime.tool_source = "lkg_cache" if runtime.tool_count else "none"
        if changed:
            logger.debug("MCP LKG 工具已加载: server=%s", config.server_id)

    async def shutdown(self) -> None:
        for task in list(self._retry_tasks.values()):
            task.cancel()
        self._retry_tasks.clear()
        for task in (self._watch_task, self._health_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._watch_task = None
        self._health_task = None
        await self.pool.shutdown()
        self._initialized = False

    # ---------- 状态查询 ----------

    def config_for(self, server_id: str) -> ServerConfig | None:
        return self._config.servers.get(server_id)

    def state_for(self, server_id: str) -> ServerState:
        runtime = self._states.get(server_id)
        return runtime.state if runtime else ServerState.DISABLED

    def runtime_for(self, server_id: str) -> ServerRuntime | None:
        return self._states.get(server_id)

    def main_server_ids(self) -> list[str]:
        return list(self._config.main_servers)

    def server_ids(self) -> list[str]:
        return list(self._config.servers)

    def details(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for server_id, runtime in self._states.items():
            data = runtime.to_dict()
            data["tool_count"] = self.registry.tool_count(server_id)
            data["tool_source"] = (
                "live_discovery"
                if self.registry.has_tools(server_id)
                and runtime.state in {ServerState.CONNECTED, ServerState.DEGRADED}
                else "lkg_cache"
                if self.registry.has_tools(server_id)
                else "none"
            )
            data["conflicts"] = {
                name: reason
                for name, reason in self.registry.conflicts().items()
            }
            result.append(data)
        return result

    def detail(self, server_id: str) -> dict[str, Any] | None:
        runtime = self._states.get(server_id)
        if runtime is None:
            return None
        data = runtime.to_dict()
        data["tool_count"] = self.registry.tool_count(server_id)
        data["tools"] = [
            meta.to_dict() for meta in self.registry.tool_metas(server_id)
        ]
        data["pool_stats"] = self.pool.stats()
        return data

    def logs(self, server_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self.observer.recent(server_id, limit=limit)

    def _record_subprocess_log(
        self,
        server_id: str,
        message: str,
        level: str,
    ) -> None:
        """stdio 子进程 stderr / 非法 stdout 行写入观测层，前端可见。"""
        self.observer.log_line(server_id, message, level=level)

    @property
    def config_error(self) -> str:
        return self._config_error

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ---------- 工具发现 ----------

    def _discover_lock(self, server_id: str) -> asyncio.Lock:
        lock = self._discover_locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._discover_locks[server_id] = lock
        return lock

    async def _discover(
        self,
        server_id: str,
        *,
        force: bool = False,
        reason: str = "first_use",
    ) -> dict[str, Any]:
        """懒连接并发现工具；失败时保留 LKG 并进入错误状态。"""
        config = self._config.servers.get(server_id)
        runtime = self._states.get(server_id)
        if config is None or runtime is None:
            raise McpError(
                f"MCP 服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                http_status=404,
            )
        if not config.enabled:
            raise McpError(
                f"MCP 服务已停用: {server_id}",
                code="MCP_UNAVAILABLE",
                category=ErrorCategory.UNAVAILABLE,
                server_id=server_id,
                state=ServerState.DISABLED,
                http_status=503,
            )
        if (
            not force
            and runtime.state in {ServerState.CONNECTED, ServerState.DEGRADED}
            and self.registry.has_tools(server_id)
        ):
            return {"state": runtime.state.value, "reused": True}

        lock = self._discover_lock(server_id)
        async with lock:
            # 双检：并发首次使用时，第二个调用不应重复建立连接。
            if (
                not force
                and runtime.state in {ServerState.CONNECTED, ServerState.DEGRADED}
                and self.registry.has_tools(server_id)
            ):
                return {"state": runtime.state.value, "reused": True}
            runtime.epoch += 1
            epoch = runtime.epoch
            runtime.state = ServerState.STARTING
            runtime.error = ""
            runtime.error_code = ""
            runtime.error_category = ""
            runtime.retry_at = ""
            self.observer.record(
                "mcp.connect",
                server_id=server_id,
                message=f"开始连接（{reason}）",
                epoch=epoch,
            )
            entry = None
            try:
                entry = await self.pool.acquire(config)
                runtime.pool_reused = entry.reused
                tools_response = await self.pool.list_tools(
                    entry,
                    timeout_ms=config.connect_timeout_ms,
                )
                metas = [
                    ToolMeta(
                        server_id=server_id,
                        remote_name=tool.name,
                        exposed_name=config.exposed_name(tool.name),
                        description=str(tool.description or ""),
                        input_schema=dict(tool.inputSchema or {}),
                        allowlisted=config.tool_allowed(tool.name),
                        source="live_discovery",
                    )
                    for tool in tools_response.tools
                ]
                changed = self.registry.register(
                    config,
                    metas,
                    source="live_discovery",
                    reserved_names=RESERVED_TOOL_NAMES,
                )
                self.registry.save_cache(server_id, metas)
                runtime.state = ServerState.CONNECTED
                runtime.connected_at = utc_now().isoformat()
                runtime.latency_ms = entry.connect_ms
                runtime.tool_count = self.registry.tool_count(server_id)
                runtime.tool_source = "live_discovery"
                runtime.retry_count = 0
                runtime.health_failures = 0
                runtime.consecutive_failures = 0
                runtime.circuit_open_until = 0.0
                runtime.error = ""
                self.observer.record(
                    "mcp.connect",
                    server_id=server_id,
                    message="连接成功",
                    epoch=epoch,
                    tool_count=len(metas),
                    connect_ms=entry.connect_ms,
                    pool_reused=runtime.pool_reused,
                )
                if changed:
                    await self._notify_structure("tool_set_changed")
                return {"state": runtime.state.value, "reused": False}
            except Exception as exc:  # noqa: BLE001
                error = classify_exception(exc, server_id=server_id)
                if runtime.epoch != epoch:
                    return {"state": runtime.state.value, "stale": True}
                runtime.error = error.message
                runtime.error_code = error.code
                runtime.error_category = error.category.value
                self.observer.record(
                    "mcp.connect",
                    server_id=server_id,
                    level="error",
                    message=f"连接失败: {error.message}",
                    epoch=epoch,
                    error_category=error.category.value,
                )
                await self._apply_failure(server_id, error)
                if runtime.state not in {
                    ServerState.NEEDS_AUTH,
                    ServerState.FAILED,
                } and self.registry.cached_metas(server_id):
                    self._register_cached(
                        config, self.registry.cached_metas(server_id)
                    )
                return {
                    "state": runtime.state.value,
                    "error": error.to_dict(),
                    "retry_at": runtime.retry_at or None,
                }
            finally:
                if entry is not None:
                    await self.pool.release(entry)

    async def tools_for_servers(
        self,
        server_ids: list[str],
        *,
        allow_connect: bool = True,
    ) -> list[Any]:
        """Agent 构图时取代理工具；只在当前 scope 内按需 discovery。"""
        if not self._initialized:
            await self.initialize()
        targets: list[str] = []
        for server_id in dict.fromkeys(server_ids):
            config = self._config.servers.get(server_id)
            runtime = self._states.get(server_id)
            if config is None or runtime is None or not config.enabled:
                continue
            if runtime.state in {
                ServerState.PENDING,
                ServerState.NEEDS_AUTH,
                ServerState.FAILED,
                ServerState.STOPPING,
            }:
                continue
            if self.registry.has_tools(server_id):
                continue
            targets.append(server_id)

        if allow_connect:
            await asyncio.gather(
                *(
                    self._discover(server_id, reason="agent_scope")
                    for server_id in targets
                ),
                return_exceptions=True,
            )
        else:
            for server_id in targets:
                config = self._config.servers.get(server_id)
                if config is None:
                    continue
                metas = self.registry.cached_metas(server_id)
                if metas:
                    self._register_cached(config, metas)

        visible: list[str] = []
        for server_id in dict.fromkeys(server_ids):
            runtime = self._states.get(server_id)
            if runtime is None:
                continue
            if runtime.state in {
                ServerState.DISABLED,
                ServerState.FAILED,
                ServerState.NEEDS_AUTH,
                ServerState.STOPPING,
            } and not self.registry.has_tools(server_id):
                continue
            if self.registry.has_tools(server_id):
                visible.append(server_id)
        return self.registry.tools_for_servers(visible)

    async def tools_for_main_agent(self) -> list[Any]:
        return await self.tools_for_servers(self.main_server_ids())

    # ---------- 调用 ----------

    async def call_tool(
        self,
        server_id: str,
        remote_name: str,
        args: dict[str, Any],
    ) -> str:
        if not self._initialized:
            await self.initialize()
        runtime = self._states.get(server_id)
        if runtime is not None and time.monotonic() < runtime.circuit_open_until:
            return McpError(
                "MCP 服务处于熔断期，请稍后重试",
                code="MCP_CIRCUIT_OPEN",
                category=ErrorCategory.CIRCUIT_OPEN,
                server_id=server_id,
                tool_name=remote_name,
                state=runtime.state,
                http_status=503,
            ).to_json()
        started = time.monotonic()
        result = await self.router.call(server_id, remote_name, args)
        duration_ms = int((time.monotonic() - started) * 1000)
        if runtime is not None:
            runtime.last_used_at = utc_now().isoformat()
            if not result.startswith('{"ok": false'):
                runtime.latency_ms = duration_ms
                runtime.consecutive_failures = 0
                threshold = (
                    runtime.config.health.degraded_latency_ms
                    or int(runtime.config.call_timeout_ms * 0.8)
                )
                if (
                    runtime.state is ServerState.DEGRADED
                    and duration_ms <= threshold
                ):
                    runtime.state = ServerState.CONNECTED
                elif (
                    runtime.state is ServerState.CONNECTED
                    and duration_ms > threshold
                ):
                    runtime.state = ServerState.DEGRADED
                    self.observer.record(
                        "mcp.state_change",
                        server_id=server_id,
                        message="调用耗时超过降级阈值",
                        state=runtime.state.value,
                        duration_ms=duration_ms,
                        threshold_ms=threshold,
                    )
        return result

    async def on_tool_failure(self, server_id: str, error: McpError) -> None:
        runtime = self._states.get(server_id)
        if runtime is None:
            return
        if error.category is ErrorCategory.TOOL:
            return
        runtime.consecutive_failures += 1
        if runtime.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            runtime.circuit_open_until = time.monotonic() + CIRCUIT_OPEN_SECONDS
            self.observer.record(
                "mcp.circuit_open",
                server_id=server_id,
                level="warning",
                open_seconds=CIRCUIT_OPEN_SECONDS,
            )
        await self._apply_failure(server_id, error)

    async def _apply_failure(self, server_id: str, error: McpError) -> None:
        runtime = self._states.get(server_id)
        config = self._config.servers.get(server_id)
        if runtime is None or config is None:
            return
        runtime.error = error.message
        runtime.error_code = error.code
        runtime.error_category = error.category.value

        if error.category is ErrorCategory.AUTH:
            runtime.state = ServerState.NEEDS_AUTH
            runtime.retry_at = ""
            changed = self.registry.unregister(server_id)
            if changed:
                await self._notify_structure("server_needs_auth")
        elif error.category in {ErrorCategory.CONFIG, ErrorCategory.PERMANENT}:
            runtime.state = ServerState.FAILED
            runtime.retry_at = ""
            changed = self.registry.unregister(server_id)
            if changed:
                await self._notify_structure("server_failed")
        elif error.category in {
            ErrorCategory.TRANSIENT,
            ErrorCategory.TIMEOUT,
            ErrorCategory.UNAVAILABLE,
        }:
            runtime.retry_count += 1
            if runtime.retry_count >= config.retry.max_attempts:
                runtime.state = ServerState.FAILED
                runtime.retry_at = ""
                changed = self.registry.unregister(server_id)
                if changed:
                    await self._notify_structure("retry_exhausted")
            else:
                runtime.state = ServerState.PENDING
                delay_ms = config.retry.delay_ms(runtime.retry_count)
                runtime.retry_at = datetime.fromtimestamp(
                    time.time() + delay_ms / 1000, tz=timezone.utc
                ).isoformat()
                await self.pool.close_key(config.pool_key)
                self._schedule_retry(server_id, runtime.epoch, delay_ms)
        else:
            runtime.state = ServerState.PENDING
        self.observer.record(
            "mcp.state_change",
            server_id=server_id,
            level="warning",
            state=runtime.state.value,
            error_category=error.category.value,
            retry_at=runtime.retry_at or None,
        )

    def _schedule_retry(self, server_id: str, epoch: int, delay_ms: int) -> None:
        existing = self._retry_tasks.pop(server_id, None)
        if existing is not None:
            existing.cancel()

        async def _retry() -> None:
            try:
                await asyncio.sleep(delay_ms / 1000)
                runtime = self._states.get(server_id)
                if runtime is None:
                    return
                if runtime.epoch != epoch or runtime.state is not ServerState.PENDING:
                    return
                await self._discover(server_id, force=True, reason="retry")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("MCP 重试任务失败: %s", server_id)

        self._retry_tasks[server_id] = asyncio.create_task(
            _retry(), name=f"mcp-retry-{server_id}"
        )

    # ---------- 管理操作 ----------

    async def probe(self, server_id: str) -> dict[str, Any]:
        result = await self._discover(server_id, force=True, reason="probe")
        detail = self.detail(server_id)
        return {"probe": result, "server": detail}

    async def restart(self, server_id: str) -> dict[str, Any]:
        config = self._config.servers.get(server_id)
        runtime = self._states.get(server_id)
        if config is None or runtime is None:
            raise McpError(
                f"MCP 服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                http_status=404,
            )
        runtime.epoch += 1
        await self.pool.close_server(config)
        runtime.retry_count = 0
        runtime.consecutive_failures = 0
        runtime.circuit_open_until = 0.0
        runtime.state = (
            ServerState.DORMANT if config.enabled else ServerState.DISABLED
        )
        runtime.error = ""
        runtime.error_code = ""
        runtime.error_category = ""
        runtime.retry_at = ""
        self.observer.record("mcp.disconnect", server_id=server_id, message="手动重启")
        return self.detail(server_id) or {}

    async def enable(self, server_id: str, enabled: bool) -> dict[str, Any]:
        old_config = self._config.servers.get(server_id)
        self._config = await asyncio.to_thread(
            self.config_store.set_enabled, server_id, enabled
        )
        config = self._config.servers.get(server_id)
        runtime = self._states.get(server_id)
        if config is None or runtime is None:
            raise McpError(
                f"MCP 服务不存在: {server_id}",
                code="MCP_SERVER_NOT_FOUND",
                category=ErrorCategory.CONFIG,
                server_id=server_id,
                http_status=404,
            )
        runtime.config = config
        if not enabled:
            runtime.state = ServerState.DISABLED
            runtime.epoch += 1
            changed = self.registry.unregister(server_id)
            # 停用后 config.connection 会被清空，pool key 随之变化；
            # 必须用停用前的 old_config 才能关闭原会话。
            await self.pool.close_server(old_config or config)
            runtime.tool_count = 0
            runtime.tool_source = "none"
            self.observer.record("mcp.disconnect", server_id=server_id, message="用户停用")
            if changed:
                await self._notify_structure("server_disabled")
        else:
            runtime.state = ServerState.DORMANT
            runtime.error = ""
            runtime.error_code = ""
            runtime.error_category = ""
            runtime.retry_at = ""
            metas = self.registry.cached_metas(server_id)
            if metas:
                self._register_cached(config, metas)
            self.observer.record("mcp.connect", server_id=server_id, message="用户启用（等待首次使用）")
            if self.registry.has_tools(server_id):
                await self._notify_structure("server_enabled")
        return self.detail(server_id) or {}

    async def upsert_server(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        old_config = self._config.servers.get(server_id)
        old_names = self.registry.exposed_names()
        self._config = await asyncio.to_thread(
            self.config_store.upsert_server, server_id, payload
        )
        self._config_error = ""
        config = self._config.servers[server_id]
        runtime = self._states.get(server_id)
        policy_changed = old_config is not None and (
            old_config.effective_namespace != config.effective_namespace
            or old_config.namespace_mode != config.namespace_mode
            or old_config.allow_tools != config.allow_tools
            or old_config.deny_tools != config.deny_tools
        )
        connection_changed = (
            old_config is not None and old_config.pool_key != config.pool_key
        )
        if runtime is None:
            runtime = ServerRuntime(
                config=config,
                state=ServerState.DORMANT if config.enabled else ServerState.DISABLED,
            )
            self._states[server_id] = runtime
        else:
            runtime.config = config
            if not config.enabled:
                runtime.state = ServerState.DISABLED
            elif runtime.state is ServerState.DISABLED:
                runtime.state = ServerState.DORMANT
            # 仅改描述/标签等元信息时保留 Connected；连接参数变化才回到 Dormant。
            if connection_changed:
                runtime.state = ServerState.DORMANT
            runtime.epoch += 1
            runtime.error = ""
            runtime.error_code = ""
            runtime.error_category = ""
            runtime.retry_at = ""

        if connection_changed or not config.enabled:
            if old_config is not None:
                await self.pool.close_server(old_config)
            self.registry.unregister(server_id)
            if connection_changed and config.enabled:
                self.registry.clear_cache(server_id)
        elif policy_changed:
            # namespace / allow / deny 变化：用同一个会话重新生成代理工具。
            metas = self.registry.tool_metas(server_id)
            if not metas:
                metas = self.registry.cached_metas(server_id)
            if metas:
                self.registry.register(
                    config,
                    metas,
                    source="live_discovery",
                    reserved_names=RESERVED_TOOL_NAMES,
                )
        elif not self.registry.has_tools(server_id):
            metas = self.registry.cached_metas(server_id)
            if metas:
                self._register_cached(config, metas)
        if self.registry.exposed_names() != old_names:
            await self._notify_structure("server_upserted")
        self.observer.record(
            "mcp.config.write",
            server_id=server_id,
            message="新增/更新服务配置",
        )
        return self.detail(server_id) or {}

    async def import_servers(
        self, entries: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """导入一组厂商配置：一次性写入 JSON，并同步运行时状态。"""
        if not entries:
            return []
        old_key = self._structure_key
        old_configs = dict(self._config.servers)
        old_names = self.registry.exposed_names()
        self._config = await asyncio.to_thread(
            self.config_store.import_servers, entries
        )
        self._config_error = ""

        for server_id, _ in entries:
            old_config = old_configs.get(server_id)
            config = self._config.servers[server_id]
            connection_changed = (
                old_config is not None and old_config.pool_key != config.pool_key
            )
            if connection_changed or not config.enabled:
                if old_config is not None:
                    await self.pool.close_server(old_config)
                self.registry.unregister(server_id)
                if connection_changed and config.enabled:
                    self.registry.clear_cache(server_id)
            runtime = self._states.get(server_id)
            if runtime is None:
                runtime = ServerRuntime(
                    config=config,
                    state=(
                        ServerState.DORMANT
                        if config.enabled
                        else ServerState.DISABLED
                    ),
                )
                self._states[server_id] = runtime
            else:
                runtime.config = config
                if not config.enabled:
                    runtime.state = ServerState.DISABLED
                elif runtime.state is ServerState.DISABLED:
                    runtime.state = ServerState.DORMANT
                if connection_changed:
                    runtime.state = ServerState.DORMANT
                runtime.epoch += 1
                runtime.error = ""
                runtime.error_code = ""
                runtime.error_category = ""
                runtime.retry_at = ""
            if not self.registry.has_tools(server_id):
                metas = self.registry.cached_metas(server_id)
                if metas:
                    self._register_cached(config, metas)

        if (
            self.registry.exposed_names() != old_names
            or self._compute_structure_key() != old_key
        ):
            await self._notify_structure("servers_imported")
        self.observer.record(
            "mcp.config.write",
            message=f"导入 {len(entries)} 个 MCP 服务配置",
        )
        return [
            self.detail(server_id) or {}
            for server_id, _ in entries
        ]

    async def delete_server(self, server_id: str) -> None:
        old_config = self._config.servers.get(server_id)
        self._config = await asyncio.to_thread(
            self.config_store.delete_server, server_id
        )
        self._config_error = ""
        if old_config is not None:
            await self.pool.close_server(old_config)
        changed = self.registry.unregister(server_id)
        self.registry.clear_cache(server_id)
        runtime = self._states.pop(server_id, None)
        if runtime is not None:
            runtime.state = ServerState.STOPPING
        task = self._retry_tasks.pop(server_id, None)
        if task is not None:
            task.cancel()
        self.observer.record(
            "mcp.config.write", server_id=server_id, message="删除服务配置"
        )
        if changed:
            await self._notify_structure("server_deleted")

    async def reload_from_disk(self) -> None:
        """配置文件变化后重新加载；不建立连接。"""
        old_key = self._structure_key
        old_configs = self._config.servers
        try:
            new_config = await asyncio.to_thread(self.config_store.load)
        except Exception as error:  # noqa: BLE001
            self.observer.record(
                "mcp.config.write",
                level="error",
                message=f"配置热更新失败，保留旧配置: {error}",
            )
            logger.exception("MCP 配置热更新失败，保留旧配置")
            return

        removed = set(old_configs) - set(new_config.servers)
        for server_id in removed:
            config = old_configs[server_id]
            await self.pool.close_server(config)
            self.registry.unregister(server_id)
            self.registry.clear_cache(server_id)
            self._states.pop(server_id, None)

        self._config = new_config
        self._config_error = ""
        self._rebuild_states(load_cache=True)
        for server_id, config in new_config.servers.items():
            runtime = self._states[server_id]
            old = old_configs.get(server_id)
            if old is not None and old.pool_key != config.pool_key:
                await self.pool.close_server(old)
                self.registry.unregister(server_id)
                runtime.state = ServerState.DORMANT
            elif old is not None:
                self.registry.unregister(server_id)
                metas = self.registry.cached_metas(server_id)
                if metas and config.enabled:
                    self._register_cached(config, metas)

        self._structure_key = self._compute_structure_key()
        self.observer.record(
            "mcp.config.write", message="配置文件已热更新", removed=sorted(removed)
        )
        if self._structure_key != old_key:
            await self._notify_structure("config_reloaded")

    def _compute_structure_key(self) -> tuple[Any, ...]:
        items: list[tuple[Any, ...]] = []
        for server_id, config in self._config.servers.items():
            items.append(
                (
                    server_id,
                    config.enabled,
                    config.effective_namespace,
                    config.namespace_mode,
                    config.namespace_separator,
                    tuple(config.allow_tools),
                    tuple(config.deny_tools),
                )
            )
        # main_servers 变化会改变主 Agent 的工具集，必须触发图重建。
        return tuple(items) + (("main_servers", tuple(self._config.main_servers)),)

    async def watch_config(self, *, interval: float = 2.0) -> None:
        """轮询配置文件；变化后重载配置，但不连接任何服务。"""
        path = self.config_store.path
        try:
            last = self._file_fingerprint(path)
        except OSError:
            last = None
        logger.info("MCP 配置监听已启动: %s（%.1fs）", path, interval)
        while True:
            await asyncio.sleep(interval)
            fingerprint = self._file_fingerprint(path)
            if fingerprint == last:
                continue
            last = fingerprint
            await self.reload_from_disk()

    @staticmethod
    def _file_fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    # ---------- 健康检查 ----------

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            for server_id, runtime in list(self._states.items()):
                if runtime.state not in {
                    ServerState.CONNECTED,
                    ServerState.DEGRADED,
                }:
                    continue
                config = self._config.servers.get(server_id)
                if config is None:
                    continue
                entry = self.pool.peek(config)
                if entry is None:
                    runtime.state = ServerState.DORMANT
                    runtime.health_failures = 0
                    continue
                if entry.ref_count > 0:
                    continue
                try:
                    await self.pool.ping(
                        entry,
                        timeout_ms=config.health.timeout_ms,
                    )
                    runtime.health_failures = 0
                    if runtime.state is ServerState.DEGRADED:
                        runtime.state = ServerState.CONNECTED
                        self.observer.record(
                            "mcp.health",
                            server_id=server_id,
                            message="健康检查恢复",
                        )
                except Exception as exc:  # noqa: BLE001
                    runtime.health_failures += 1
                    error = classify_exception(exc, server_id=server_id)
                    self.observer.record(
                        "mcp.health",
                        server_id=server_id,
                        level="warning",
                        message="健康检查失败",
                        failures=runtime.health_failures,
                        error_category=error.category.value,
                    )
                    if runtime.health_failures >= config.health.failure_threshold:
                        await self.pool.close_key(config.pool_key)
                        await self._apply_failure(server_id, error)

    # ---------- 结构变更通知 ----------

    def set_structure_handler(self, handler: Any) -> None:
        self._structure_handler = handler

    async def _notify_structure(self, reason: str) -> None:
        self._structure_key = self._compute_structure_key()
        handler = self._structure_handler
        if handler is None:
            return
        self.observer.record(
            "mcp.state_change", message=f"工具结构变化：{reason}"
        )
        logger.info("MCP 工具结构变化（%s），将重建 Agent 图", reason)
        result = handler()
        if asyncio.iscoroutine(result):
            await result


mcp_runtime = MCPRuntime()
