"""MCP 工具注册表与热更新代理。

设计要点：

* **代理工具**：Agent 图里绑定的是 :class:`MCPProxyTool`，名字与工具对象
  生命周期固定；真正执行时才去注册表取当前实现。因此服务重启、工具内部
  实现变化、描述 / 参数 Schema 变化都能在下一轮对话生效，不需要重建图。
* **差量刷新**：:meth:`MCPToolHub.refresh` 按服务配置指纹做差异比较，只重载
  变化的服务；未变化的服务继续复用原代理对象，刷新期间旧工具始终可用。
* **结构变更**：工具名集合增删无法只靠代理解决（LangGraph 的 ToolNode 在
  构图时固定了可执行工具表），因此会通过回调通知宿主重建 Agent 图。
* **文件监听**：:meth:`MCPToolHub.watch` 轮询 ``mcp_servers.yaml`` 的
  mtime，变化后自动刷新，让设置页改配置即生效。

MCP 工具本身每次调用都会新建会话（不持有长连接），代理复用是安全的。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from langchain_core.tools import BaseTool, ToolException

from agent.tools.mcp_client import (
    load_mcp_config,
    load_mcp_tools_for_servers,
    mcp_config_path,
)

logger = logging.getLogger(__name__)

# 文件监听轮询间隔；只做一次 stat，开销可以忽略。
DEFAULT_WATCH_INTERVAL_SECONDS = 2.0


class MCPRefreshReport(NamedTuple):
    """一次刷新的结果。"""

    servers: dict[str, int]
    """刷新后每个服务的工具数。"""

    loaded: list[str]
    """本次真正重新加载的服务。"""

    failed: list[str]
    """本次加载失败的服务（工具已被清空）。"""

    structure_changed: bool
    """工具名集合是否变化；为真时调用方应重建已缓存的 Agent 图。"""


class MCPProxyTool(BaseTool):
    """指向注册表的稳定代理工具。

    图里固定持有本对象；``description`` / ``args_schema`` 会在热刷新时原地
    更新，``_arun`` 则转发给注册表里的当前实现。
    """

    server: str
    remote_name: str

    def bind_live(self, live: BaseTool) -> None:
        """把代理的对外元信息同步为当前实现。"""
        description = str(getattr(live, "description", "") or "")
        if self.description != description:
            self.description = description
        args_schema = getattr(live, "args_schema", None)
        if self.args_schema is not args_schema:
            self.args_schema = args_schema

    async def _arun(self, **kwargs: Any) -> Any:
        live = mcp_hub.live_tool(self.server, self.remote_name)
        if live is None:
            raise ToolException(
                f"MCP 工具 {self.remote_name} 已随 mcp_servers.yaml 变更移除，"
                "当前任务无法再调用它；请改用其它工具，或让用户开启对应服务。"
            )
        return await live.ainvoke(kwargs)

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"MCP 工具 {self.remote_name} 只支持异步调用"
        )


class MCPToolHub:
    """进程级 MCP 工具注册表，支持差量热刷新。"""

    def __init__(self) -> None:
        self._signatures: dict[str, str] = {}
        self._tools: dict[str, dict[str, MCPProxyTool]] = {}
        self._live_targets: dict[str, dict[str, BaseTool]] = {}
        self._generation = 0
        self._warmed = False
        self._last_file_fingerprint: tuple[int, int] | None = None
        self._last_structure_changed = False
        self._lock: asyncio.Lock | None = None

    # ---------- 只读视图 ----------

    @property
    def warmed(self) -> bool:
        """是否已经完成过至少一次刷新。"""
        return self._warmed

    @property
    def generation(self) -> int:
        """每次刷新自增，便于调用方判断是否需要更新工具列表。"""
        return self._generation

    def status(self) -> dict[str, int]:
        """返回 ``{server_name: tool_count}``。"""
        return {name: len(tools) for name, tools in self._tools.items()}

    def live_tool(self, server: str, name: str) -> BaseTool | None:
        """按服务名 + 工具名取当前实现；已被移除时返回 ``None``。"""
        proxy = self._tools.get(server, {}).get(name)
        if proxy is None:
            return None
        return self._live_targets.get(server, {}).get(name)

    def tool_list(self, server_names: Iterable[str]) -> list[BaseTool]:
        """取指定服务的全部代理工具；未启用或未加载的服务直接跳过。"""
        names = list(dict.fromkeys(str(name) for name in server_names if str(name)))
        tools: list[BaseTool] = []
        for name in names:
            server_tools = self._tools.get(name)
            if server_tools is None:
                if self._warmed:
                    logger.info("MCP Server %s 未启用或未加载，跳过工具绑定", name)
                continue
            tools.extend(server_tools.values())
        _assert_unique_names(tools)
        return tools

    # ---------- 刷新 ----------

    def _lock_or_create(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def refresh(
        self,
        *,
        config_path: str | Path | None = None,
        force: bool = False,
    ) -> MCPRefreshReport:
        """按配置指纹差量刷新；``force=True`` 时重载全部已启用服务。"""
        async with self._lock_or_create():
            if not force and config_path is None:
                # 同一进程可能有多个监听者（HTTP 与 gRPC 各一个 loader），
                # 用文件指纹去重，避免同一次改动被重复加载。
                fingerprint = _file_fingerprint(mcp_config_path(None))
                if (
                    fingerprint is not None
                    and fingerprint == self._last_file_fingerprint
                ):
                    return MCPRefreshReport(
                        servers=self.status(),
                        loaded=[],
                        failed=[],
                        # 沿用上一次的结果，让其他 loader 也能清掉本进程的图缓存。
                        structure_changed=self._last_structure_changed,
                    )
                self._last_file_fingerprint = fingerprint

            config = load_mcp_config(config_path)
            servers = {
                name: server
                for name, server in config["servers"].items()
                if server.get("enabled", True)
            }
            signatures = {
                name: _server_signature(server) for name, server in servers.items()
            }

            changed = [
                name
                for name, signature in signatures.items()
                if force or self._signatures.get(name) != signature
            ]
            removed = sorted(set(self._signatures) - set(signatures))
            before = self._name_set()

            started = time.monotonic()
            results = await asyncio.gather(
                *(
                    load_mcp_tools_for_servers([name], config_path=config_path)
                    for name in changed
                ),
                return_exceptions=True,
            )

            loaded: list[str] = []
            failed: list[str] = []
            for name, result in zip(changed, results):
                if isinstance(result, BaseException):
                    logger.error("MCP Server %s 刷新失败，本次移除其工具: %s", name, result)
                    self._apply_server(name, [])
                    failed.append(name)
                    continue
                self._apply_server(name, list(result))
                loaded.append(name)

            for name in removed:
                logger.info("MCP Server %s 已停用或删除，移除其工具", name)
                self._tools.pop(name, None)
                self._live_targets.pop(name, None)
                self._signatures.pop(name, None)

            # 只保留当前启用的服务，避免残留服务继续出现在工具清单里。
            for name in list(self._tools):
                if name not in signatures:
                    self._tools.pop(name, None)
                    self._live_targets.pop(name, None)
                    self._signatures.pop(name, None)

            self._signatures = signatures
            self._generation += 1
            self._warmed = True

            structure_changed = before != self._name_set()
            self._last_structure_changed = structure_changed
            elapsed = time.monotonic() - started
            if changed or removed:
                logger.info(
                    "MCP 工具热更新：重载 %s，移除 %s，耗时 %.1fs，当前 %s",
                    loaded or "无",
                    removed or "无",
                    elapsed,
                    self.status(),
                )
            else:
                logger.debug("MCP 配置无变化，跳过刷新")

            return MCPRefreshReport(
                servers=self.status(),
                loaded=loaded,
                failed=failed,
                structure_changed=structure_changed,
            )

    def _apply_server(self, server: str, tools: list[BaseTool]) -> None:
        """把一次加载结果合并进注册表：原地更新代理，新增工具建代理。"""
        existing = self._tools.setdefault(server, {})
        live_targets = self._live_targets.setdefault(server, {})
        refreshed: dict[str, MCPProxyTool] = {}
        refreshed_live: dict[str, BaseTool] = {}
        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            proxy = existing.get(name)
            if proxy is None:
                proxy = MCPProxyTool(
                    name=name,
                    description=str(getattr(tool, "description", "") or ""),
                    args_schema=getattr(tool, "args_schema", None),
                    server=server,
                    remote_name=name,
                    handle_tool_error=True,
                )
            else:
                proxy.bind_live(tool)
            refreshed[name] = proxy
            refreshed_live[name] = tool
        self._tools[server] = refreshed
        self._live_targets[server] = refreshed_live

    def _name_set(self) -> set[tuple[str, str]]:
        return {
            (server, name)
            for server, tools in self._tools.items()
            for name in tools
        }

    def reset(self) -> None:
        """清空全部状态，下次刷新会重新发现。"""
        self._signatures.clear()
        self._tools.clear()
        self._live_targets.clear()
        self._generation += 1
        self._warmed = False
        self._last_file_fingerprint = None
        self._lock = None

    # ---------- 文件监听 ----------

    async def watch(
        self,
        *,
        on_structure_change: Callable[[], Awaitable[None]] | None = None,
        interval: float = DEFAULT_WATCH_INTERVAL_SECONDS,
        config_path: str | Path | None = None,
    ) -> None:
        """轮询配置文件，变化后热刷新；工具名集合变化时回调宿主重建图。"""
        path = mcp_config_path(config_path)
        last = _file_fingerprint(path)
        logger.info("MCP 配置监听已启动：%s（%.1fs 轮询）", path, interval)
        while True:
            await asyncio.sleep(interval)
            fingerprint = _file_fingerprint(path)
            if fingerprint == last:
                continue
            last = fingerprint
            try:
                report = await self.refresh(config_path=config_path)
            except Exception:
                logger.exception("MCP 配置热更新失败，保留当前工具集")
                continue
            if report.structure_changed and on_structure_change is not None:
                try:
                    await on_structure_change()
                except Exception:
                    logger.exception("MCP 结构变更回调失败")


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    """返回 (mtime_ns, size)，文件不存在时返回 None。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _server_signature(server: dict[str, Any]) -> str:
    """服务配置指纹；连接参数、env 变化都会触发重载。"""
    return json.dumps(server, sort_keys=True, ensure_ascii=False, default=str)


def _assert_unique_names(tools: Iterable[BaseTool]) -> None:
    names = [str(getattr(tool, "name", "")) for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"MCP 工具名称重复: {duplicates}")


mcp_hub = MCPToolHub()


def is_mcp_warmed() -> bool:
    """预热是否已完成；未完成时应回落到即时加载。"""
    return mcp_hub.warmed


def cached_mcp_tools(server_names: Iterable[str]) -> list[BaseTool]:
    """同步从注册表取代理工具，不触发任何服务启动。"""
    return mcp_hub.tool_list(server_names)


def cached_main_mcp_tools(
    *,
    config_path: str | Path | None = None,
) -> list[BaseTool]:
    """同步取 ``main_servers`` 对应的代理工具。"""
    config = load_mcp_config(config_path)
    return mcp_hub.tool_list(config["main_servers"])
