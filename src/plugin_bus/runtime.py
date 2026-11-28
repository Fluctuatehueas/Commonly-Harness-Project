"""按用户隔离的插件运行时门面。

对上层只暴露：已安装列表、工具清单、调用、启停、健康、能力与提示词注入。
进程与连接由 ``PluginLifecycleManager`` 管理；本模块不直接操作传输。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import BaseTool

from plugin_bus import storage
from plugin_bus.errors import PluginError, PluginNotFound, PluginNotReady
from plugin_bus.lifecycle import PluginLifecycleManager
from plugin_bus.manifest import Manifest
from plugin_bus.registry import CapabilityRegistry, build_args_schema
from plugin_bus.storage import InstalledPlugin
from plugin_bus.store import PluginStore
from plugin_bus.transport.inprocess import load_file_target

logger = logging.getLogger(__name__)

# 插件工具统一前缀，避免与内置工具、MCP 命名空间冲突。
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
    "plugin_resource_list",
    "plugin_resource_read",
}


def _config() -> Any:
    from agent import config

    return config


class PluginResourceListTool(BaseTool):
    """列举当前用户已启用插件的资源目录。"""

    name: str = "plugin_resource_list"
    description: str = (
        "列出已启用插件提供的外部资源（uri、名称、来源插件）。"
        "需要读取资源正文时调用 plugin_resource_read。"
    )
    user_id: str

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("插件资源工具只支持异步调用")

    async def _arun(self, **kwargs: Any) -> Any:
        runtime = get_plugin_runtime(self.user_id)
        return json.dumps(await runtime.resources_overview(), ensure_ascii=False)


class PluginResourceReadTool(BaseTool):
    """读取某个插件提供的资源正文。"""

    name: str = "plugin_resource_read"
    description: str = "读取插件资源正文，需要提供 plugin_id 与 uri。"
    user_id: str

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("插件资源工具只支持异步调用")

    async def _arun(self, plugin_id: str, uri: str) -> Any:
        runtime = get_plugin_runtime(self.user_id)
        return json.dumps(
            await runtime.read_resource(plugin_id, uri), ensure_ascii=False
        )


class PluginRuntime:
    """单个用户的插件运行时。"""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.store = PluginStore()
        self.registry = CapabilityRegistry()
        self.registry.load(user_id)
        self.manager = PluginLifecycleManager(
            user_id=user_id,
            store=self.store,
            registry=self.registry,
            reserved_names=set(RESERVED_TOOL_NAMES),
            sandbox_provider=self._sandbox_provider,
            max_processes=_config().PLUGIN_MAX_PROCESSES,
            idle_ttl_seconds=_config().PLUGIN_IDLE_TTL_SECONDS,
            start_timeout=_config().PLUGIN_START_TIMEOUT_SECONDS,
            call_timeout=_config().PLUGIN_CALL_TIMEOUT_SECONDS,
            auto_disable_after=_config().PLUGIN_AUTO_DISABLE_AFTER_FAILURES,
        )
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._idle_task: asyncio.Task[Any] | None = None
        self._installed: dict[str, InstalledPlugin] = {}
        self._enabled: dict[str, bool] = {}
        self._installs: dict[str, list[dict[str, Any]]] = {}

    # ---------- 初始化 ----------

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            store_ready = True
            try:
                await self.store.setup()
            except Exception:
                store_ready = False
                logger.warning("插件表初始化失败，运行时以降级状态启动", exc_info=True)
            await self._reconcile(store_ready=store_ready)
            await self.manager.start_health_loop()
            self._idle_task = asyncio.create_task(
                self._idle_loop(), name=f"plugin-idle-{self.user_id}"
            )
            self._initialized = True

    async def _reconcile(self, *, store_ready: bool) -> None:
        self._installed = storage.discover_installed(self.user_id)
        # 内置 inprocess 插件：按 manifest.entry 从插件目录加载入口对象。
        for plugin_id, item in self._installed.items():
            if item.manifest.runtime.kind != "inprocess":
                continue
            try:
                target = load_file_target(
                    item.path, item.manifest.runtime.entry or "plugin"
                )
            except Exception:
                logger.warning(
                    "内置插件入口加载失败: %s (%s)", plugin_id, item.path, exc_info=True
                )
                continue
            self.manager.register_inprocess(plugin_id, target)
        states: dict[str, dict[str, Any]] = {}
        installs: list[dict[str, Any]] = []
        if store_ready:
            try:
                states = {
                    str(row["plugin_id"]): dict(row)
                    for row in await self.store.list_user_states(self.user_id)
                }
                installs = await self.store.list_installs(self.user_id)
            except Exception:
                logger.warning("读取插件状态失败，按默认启用处理", exc_info=True)
        # 远程插件没有磁盘包，manifest 存在 plugin_config 的保留键里。
        for row in installs:
            if str(row.get("runtime_kind") or "") != "remote":
                continue
            plugin_id = str(row.get("plugin_id") or "")
            if not plugin_id or plugin_id in self._installed:
                continue
            try:
                config = await self.store.get_config(self.user_id, plugin_id)
                raw_manifest = config.get("__manifest__")
                if not isinstance(raw_manifest, dict):
                    continue
                from plugin_bus.manifest import manifest_from_dict

                manifest = manifest_from_dict(
                    raw_manifest, source=str(row.get("source") or "remote")
                )
                self._installed[plugin_id] = InstalledPlugin(
                    plugin_id=plugin_id,
                    version=str(row.get("version") or manifest.version),
                    path=Path(""),
                    source=str(row.get("source") or "remote"),
                    manifest=manifest,
                )
            except Exception:
                logger.debug("远程插件 manifest 恢复失败: %s", plugin_id, exc_info=True)
        self.manager.set_installed(self._installed)
        self._installs = {}
        for row in installs:
            self._installs.setdefault(str(row["plugin_id"]), []).append(dict(row))
        self._enabled = {
            plugin_id: bool(states.get(plugin_id, {}).get("enabled", True))
            for plugin_id in self._installed
        }
        for plugin_id, state in self.manager.states().items():
            state.status = "enabled" if self._enabled.get(plugin_id, True) else "disabled"
        if not store_ready:
            return
        # 磁盘有包、PG 缺记录时自愈补登记。
        known = {(row["plugin_id"], row["version"]) for row in installs}
        for plugin_id, item in self._installed.items():
            if item.source == "builtin" or item.source == "project":
                continue
            if (plugin_id, item.version) in known:
                continue
            try:
                await self.store.upsert_install(
                    user_id=self.user_id,
                    plugin_id=plugin_id,
                    version=item.version,
                    source=item.source,
                    runtime_kind=item.manifest.runtime.kind,
                    transport_kind=item.manifest.transport.kind,
                    endpoint=item.manifest.transport.url,
                    sha256=str(item.manifest.dist.get("sha256") or ""),
                    status="verified",
                )
                await self.store.audit(
                    user_id=self.user_id,
                    plugin_id=plugin_id,
                    version=item.version,
                    action="repair",
                    result="ok",
                    detail={"reason": "disk_without_db"},
                )
            except Exception:
                logger.debug("插件记录补登记失败: %s", plugin_id, exc_info=True)

    async def _sandbox_provider(self, user_id: str) -> Any:
        from api.agent_loader import agent_loader

        return await agent_loader.get_sandbox_backend(user_id)

    async def refresh_installed(self) -> None:
        """安装/卸载后重新扫描磁盘与状态；安装流程复用。"""
        await self._reconcile(store_ready=True)

    def register_remote_manifest(self, manifest: Manifest) -> None:
        """远程插件没有磁盘目录，用内存 manifest 参与启动。"""
        self._installed[manifest.plugin_id] = InstalledPlugin(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            path=Path(""),
            source="remote",
            manifest=manifest,
        )
        self.manager.set_installed(self._installed)
        self._enabled[manifest.plugin_id] = True

    # ---------- 查询 ----------

    def enabled_plugin_ids(self) -> list[str]:
        return [
            plugin_id
            for plugin_id in self._installed
            if self._enabled.get(plugin_id, True)
        ]

    def installed_map(self) -> dict[str, InstalledPlugin]:
        return dict(self._installed)

    async def tools_for_main_agent(
        self, plugin_ids: list[str] | None = None
    ) -> list[BaseTool]:
        await self.initialize()
        selected = {str(item) for item in (plugin_ids or []) if str(item).strip()}
        tools = self.registry.tools_for_user(
            self.user_id, reserved_names=set(RESERVED_TOOL_NAMES)
        )
        tools = [
            tool
            for tool in tools
            if self._enabled.get(getattr(tool, "plugin_id", ""), True)
            and (not selected or getattr(tool, "plugin_id", "") in selected)
        ]
        # 只要有启用插件声明 resource，就注入成对的资源工具。
        has_resources = any(
            self.registry.snapshot(self.user_id, plugin_id)
            and self.registry.snapshot(self.user_id, plugin_id).resources
            for plugin_id in self.enabled_plugin_ids()
            if not selected or plugin_id in selected
        )
        if has_resources:
            tools.extend(
                [
                    PluginResourceListTool(user_id=self.user_id),
                    PluginResourceReadTool(
                        user_id=self.user_id,
                        args_schema=build_args_schema(
                            {
                                "type": "object",
                                "properties": {
                                    "plugin_id": {"type": "string"},
                                    "uri": {"type": "string"},
                                },
                                "required": ["plugin_id", "uri"],
                            },
                            "plugin_resource_read",
                        ),
                    ),
                ]
            )
        return tools

    async def call_tool(
        self, plugin_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        await self.initialize()
        if not self._enabled.get(plugin_id, True):
            raise PluginNotReady(f"插件已停用: {plugin_id}", plugin_id=plugin_id)
        try:
            return await self.manager.call_tool(plugin_id, tool_name, arguments)
        except PluginError as error:
            return f"Error: {error.message}"
        except Exception as error:  # noqa: BLE001 - 工具层必须返回文本
            logger.exception("插件工具调用异常: %s/%s", plugin_id, tool_name)
            return f"Error: 插件工具调用失败: {error}"

    async def resources_overview(
        self, plugin_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        await self.initialize()
        selected = {str(item) for item in (plugin_ids or []) if str(item).strip()}
        catalog = self.registry.resource_catalog(self.user_id)
        return [
            item
            for item in catalog
            if self._enabled.get(str(item.get("plugin_id")), True)
            and (not selected or str(item.get("plugin_id")) in selected)
        ]

    async def read_resource(self, plugin_id: str, uri: str) -> Any:
        await self.initialize()
        if not self._enabled.get(plugin_id, True):
            raise PluginNotReady(f"插件已停用: {plugin_id}", plugin_id=plugin_id)
        return await self.manager.read_resource(plugin_id, uri)

    async def prompt_section(
        self, message_text: str = "", plugin_ids: list[str] | None = None
    ) -> str:
        """把插件提示词与资源目录拼成系统提示片段（≤2000 字符）。"""
        await self.initialize()
        selected = {str(item) for item in (plugin_ids or []) if str(item).strip()}
        prompts = [
            item
            for item in self.registry.prompts_for_user(self.user_id, message_text)
            if self._enabled.get(str(item.get("plugin_id")), True)
            and (not selected or str(item.get("plugin_id")) in selected)
        ]
        resources = await self.resources_overview(plugin_ids)
        lines: list[str] = []
        for item in prompts:
            content = str(item.get("content") or item.get("prompt") or "").strip()
            if not content:
                plugin_id = str(item.get("plugin_id") or "")
                prompt_id = str(item.get("id") or "")
                if plugin_id and prompt_id:
                    try:
                        fetched = await self.manager.get_prompt(plugin_id, prompt_id)
                        if isinstance(fetched, dict):
                            content = str(
                                fetched.get("content") or fetched.get("prompt") or ""
                            ).strip()
                        elif isinstance(fetched, str):
                            content = fetched.strip()
                    except Exception:
                        logger.debug(
                            "获取插件提示词失败: %s/%s", plugin_id, prompt_id, exc_info=True
                        )
            if content:
                lines.append(content)
        if resources:
            lines.append("插件资源（需要正文时调用 plugin_resource_read）：")
            for item in resources[:30]:
                lines.append(
                    f"- {item.get('plugin_id')} | {item.get('uri')} | {item.get('name')}"
                )
        text = "\n".join(lines).strip()
        return text[:2000]

    async def list_installed(self) -> list[dict[str, Any]]:
        await self.initialize()
        items: list[dict[str, Any]] = []
        for plugin_id, installed in self._installed.items():
            state = self.manager.state(plugin_id)
            snapshot = self.registry.snapshot(self.user_id, plugin_id)
            install_rows = self._installs.get(plugin_id, [])
            status = state.status if state else "enabled"
            if not self._enabled.get(plugin_id, True):
                status = "disabled"
            items.append(
                {
                    "plugin_id": plugin_id,
                    "name": installed.manifest.name,
                    "version": installed.version,
                    "description": installed.manifest.description,
                    "author": {
                        "name": installed.manifest.author_name,
                        "homepage": installed.manifest.author_homepage,
                    },
                    "tag": list(installed.manifest.tags),
                    "license": installed.manifest.license,
                    "source": installed.source,
                    "enabled": self._enabled.get(plugin_id, True),
                    "status": status,
                    "capabilities": list(installed.manifest.capabilities),
                    "permissions": list(installed.manifest.permissions),
                    "runtime": installed.manifest.runtime.to_dict(),
                    "transport": installed.manifest.transport.to_dict(),
                    "protocolVersion": installed.manifest.protocol_version,
                    "path": str(installed.path),
                    "builtin": installed.source == "builtin",
                    "deletable": installed.source != "builtin",
                    "has_ui": installed.manifest.has_capability("ui"),
                    "ui_slots": [slot.__dict__ for slot in installed.manifest.ui_slots],
                    "versions": storage.list_versions(self.user_id, plugin_id),
                    "tool_count": len(snapshot.tools) if snapshot else 0,
                    "error": state.error if state else "",
                    "installed_at": (
                        install_rows[0].get("installed_at").isoformat()
                        if install_rows
                        and getattr(install_rows[0].get("installed_at"), "isoformat", None)
                        else ""
                    ),
                }
            )
        items.sort(key=lambda item: (not item["builtin"], item["name"].casefold()))
        return items

    async def detail(self, plugin_id: str) -> dict[str, Any]:
        for item in await self.list_installed():
            if item["plugin_id"] == plugin_id:
                snapshot = self.registry.snapshot(self.user_id, plugin_id)
                item["capabilities_detail"] = (
                    snapshot.describe.get("capabilities") if snapshot else {}
                )
                item["health"] = await self.manager.health(plugin_id)
                return item
        raise PluginNotFound(plugin_id)

    async def capabilities(self, plugin_id: str) -> dict[str, Any]:
        await self.initialize()
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        snapshot = self.registry.snapshot(self.user_id, plugin_id)
        if snapshot is None and self._enabled.get(plugin_id, True):
            try:
                snapshot = await self.manager.start(plugin_id)
            except Exception:
                snapshot = None
        return {
            "plugin_id": plugin_id,
            "version": installed.version,
            "declared": list(installed.manifest.capabilities),
            "describe": snapshot.describe if snapshot else {},
        }

    async def health(self, plugin_id: str) -> dict[str, Any]:
        await self.initialize()
        if plugin_id not in self._installed:
            raise PluginNotFound(plugin_id)
        result = await self.manager.health(plugin_id)
        result["enabled"] = self._enabled.get(plugin_id, True)
        return result

    async def list_for_command(self, project_root: Path | None = None) -> list[dict[str, Any]]:
        await self.initialize()
        items = [
            {
                "plugin_id": item["plugin_id"],
                "name": item["name"],
                "source": item["source"],
                "enabled": item["enabled"],
                "description": item["description"],
            }
            for item in await self.list_installed()
        ]
        for plugin_id, installed in storage.discover_project_plugins(project_root).items():
            items.append(
                {
                    "plugin_id": plugin_id,
                    "name": installed.manifest.name,
                    "source": "project",
                    "enabled": True,
                    "description": installed.manifest.description,
                }
            )
        return items

    def project_runner(self, project_root: Path) -> Any:
        """项目级插件运行器：只在沙箱内一次性执行，不注入全局运行时。"""
        from plugin_bus.project_runner import ProjectPluginRunner

        return ProjectPluginRunner(
            user_id=self.user_id,
            project_root=project_root,
            sandbox_provider=self._sandbox_provider,
        )

    async def list_project_plugins(self, project_root: Path) -> list[dict[str, Any]]:
        return [
            item.to_dict() for item in self.project_runner(project_root).list_plugins()
        ]

    # ---------- 变更 ----------

    async def toggle(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        await self.initialize()
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        if enabled:
            await self.manager.start(plugin_id, force=True)
            await self.store.upsert_user_state(
                user_id=self.user_id,
                plugin_id=plugin_id,
                enabled=True,
                current_version=installed.version,
                auto_disabled=False,
            )
            self._enabled[plugin_id] = True
        else:
            await self.manager.mark_disabled(plugin_id, auto=False)
            self._enabled[plugin_id] = False
        await self.store.audit(
            user_id=self.user_id,
            plugin_id=plugin_id,
            version=installed.version,
            action="enable" if enabled else "disable",
            result="ok",
        )
        await self._notify_structure("toggle")
        return await self.detail(plugin_id)

    async def restart(self, plugin_id: str) -> dict[str, Any]:
        await self.initialize()
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        await self.manager.restart(plugin_id)
        await self.store.audit(
            user_id=self.user_id,
            plugin_id=plugin_id,
            version=installed.version,
            action="restart",
            result="ok",
        )
        return await self.health(plugin_id)

    async def uninstall(self, plugin_id: str) -> None:
        await self.initialize()
        installed = self._installed.get(plugin_id)
        if installed is None:
            raise PluginNotFound(plugin_id)
        if installed.source == "builtin":
            from plugin_bus.errors import PluginConflict

            raise PluginConflict("内置插件不可卸载，只能停用", plugin_id=plugin_id)
        await self.manager.stop(plugin_id, status="disabled")
        storage.remove_plugin_versions(self.user_id, plugin_id)
        await self.store.delete_installs(self.user_id, plugin_id)
        await self.store.delete_user_state(self.user_id, plugin_id)
        await self.store.delete_config(self.user_id, plugin_id)
        await self.store.audit(
            user_id=self.user_id,
            plugin_id=plugin_id,
            version=installed.version,
            action="uninstall",
            result="ok",
            detail={"source": installed.source},
        )
        self._installed.pop(plugin_id, None)
        self._enabled.pop(plugin_id, None)
        self.registry.remove(self.user_id, plugin_id)
        self.registry.save(self.user_id)
        await self._notify_structure("uninstall")

    async def save_config(
        self, plugin_id: str, config: dict[str, Any], secret_ref: str = ""
    ) -> dict[str, Any]:
        await self.initialize()
        if plugin_id not in self._installed:
            raise PluginNotFound(plugin_id)
        await self.store.save_config(
            user_id=self.user_id,
            plugin_id=plugin_id,
            config=config,
            secret_ref=secret_ref,
        )
        await self.store.audit(
            user_id=self.user_id,
            plugin_id=plugin_id,
            action="permission_change",
            result="ok",
            detail={"keys": sorted(config.keys())},
        )
        return await self.store.get_config(self.user_id, plugin_id)

    async def get_config(self, plugin_id: str) -> dict[str, Any]:
        await self.initialize()
        if plugin_id not in self._installed:
            raise PluginNotFound(plugin_id)
        return await self.store.get_config(self.user_id, plugin_id)

    # ---------- 内部 ----------

    async def invoke_hooks(
        self,
        hook_name: str,
        payload: dict[str, Any],
        *,
        timeout: float = 3.0,
        plugin_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        selected = {str(item) for item in (plugin_ids or []) if str(item).strip()}
        results: list[dict[str, Any]] = []
        for hook in self.registry.hooks_for_user(self.user_id, hook_name):
            plugin_id = str(hook.get("plugin_id") or "")
            if not self._enabled.get(plugin_id, True):
                continue
            if selected and plugin_id not in selected:
                continue
            result = await self.manager.invoke_hook(
                plugin_id, hook_name, payload, timeout=timeout
            )
            if result is not None:
                results.append({"plugin_id": plugin_id, "result": result})
        return results

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.manager.recycle_idle()
            except Exception:
                logger.debug("插件空闲回收失败", exc_info=True)

    async def _notify_structure(self, reason: str) -> None:
        handler = _STRUCTURE_HANDLER
        if handler is None:
            return
        logger.info("插件工具结构变化（%s），将重建 Agent 图", reason)
        result = handler()
        if asyncio.iscoroutine(result):
            await result

    async def shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except (asyncio.CancelledError, Exception):
                pass
            self._idle_task = None
        await self.manager.shutdown()
        await self.store.close()


# ============================================================
# 按用户隔离的运行时池
# ============================================================

_USER_RUNTIMES: dict[str, PluginRuntime] = {}
_STRUCTURE_HANDLER: Callable[[], Any] | None = None


def get_plugin_runtime(user_id: str | None = None) -> PluginRuntime:
    from agent.identity import current_user_id, safe_user_id

    uid = safe_user_id(user_id or current_user_id())
    runtime = _USER_RUNTIMES.get(uid)
    if runtime is None:
        runtime = PluginRuntime(uid)
        _USER_RUNTIMES[uid] = runtime
        logger.info("已创建用户级插件运行时：user=%s", uid)
    return runtime


def set_plugin_structure_handler(handler: Callable[[], Any] | None) -> None:
    global _STRUCTURE_HANDLER
    _STRUCTURE_HANDLER = handler


def all_plugin_runtimes() -> list[PluginRuntime]:
    return list(_USER_RUNTIMES.values())


async def shutdown_plugin_runtimes() -> None:
    for runtime in list(_USER_RUNTIMES.values()):
        try:
            await runtime.shutdown()
        except Exception:
            logger.debug("关闭插件运行时失败", exc_info=True)
    _USER_RUNTIMES.clear()


__all__ = [
    "PluginRuntime",
    "get_plugin_runtime",
    "set_plugin_structure_handler",
    "all_plugin_runtimes",
    "shutdown_plugin_runtimes",
    "RESERVED_TOOL_NAMES",
]
