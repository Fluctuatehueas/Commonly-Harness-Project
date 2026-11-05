"""Agent 的加载、用户分组和会话配置管理。

本模块有两层状态：

* ``_user_groups`` 是当前 Python 服务进程的内存缓存，键为 ``user_id``。
  同一用户在当前进程内只创建一个 Agent，减少重复构建图和后端的开销。
* 每个用户分组中的 ``thread_ids`` 是该用户使用过的会话 ID 集合。
  调用 Agent 时传入的 ``thread_id`` 会由 PostgreSQL checkpointer 用来区分会话。

``thread_id`` 是业务会话 ID，不是操作系统进程 ID。
会话写操作的互斥仅在单个服务进程内有效，当前只支持单 worker 部署。
PostgreSQL 提供跨重启持久化；多 worker 部署前必须增加分布式协调。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import HTTPException
from langchain_core.messages import AIMessage

from agent.backends.sandbox_manager import SandboxManager
from agent import config as agent_config
from agent.config import (
    close_async_persistence,
    create_async_persistence,
)
from agent.history_reader import ThreadHistoryReader
from agent.schema import AsyncTaskBinding, UserGroup
from agent.trajectory_store import delete_trajectory, list_trajectory_events
from agent.usage_store import delete_usage, read_usage


logger = logging.getLogger(__name__)


AgentFactory = Callable[..., Awaitable[Any]]
SESSION_NAMESPACE_PREFIX = ("sessions",)
ASYNC_TASK_NAMESPACE_PREFIX = ("async_tasks",)
ASYNC_TASK_MESSAGE_PREFIX = "async-task-result:"


class AgentLoader:
    """管理 Agent 生命周期，并按用户复用当前进程中的 Agent 实例。

    不同 ``user_id`` 会构造不同 Agent，记忆按 ``user_id`` 命名空间隔离。
    同一 ``user_id`` 的不同 ``thread_id`` 共用 Agent，但 checkpoint 相互独立。

    PostgreSQL 负责跨进程、跨重启的持久化；本类只保存当前 Python 进程的缓存，
    不能作为多进程共享状态使用。
    """

    def __init__(self) -> None:
        self._user_groups: dict[str, UserGroup] = {}
        self._lock = asyncio.Lock()
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._active_threads: set[str] = set()
        self._initialized = False
        self._agent_factory: AgentFactory | None = None
        self._store: Any | None = None
        self._checkpointer: Any | None = None
        self._history_reader: ThreadHistoryReader | None = None
        self._sandbox_manager: SandboxManager | None = None
        self._mcp_watch_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """初始化持久化资源和 Agent 工厂，并在后台调度沙箱预热。"""
        async with self._lock:
            if self._initialized:
                return
            from agent.main_agent import create_main_agent
            from agent.mcp.runtime import mcp_runtime

            # 启动阶段只加载 MCP 配置与 LKG 工具缓存，不建立任何连接。
            # 真正的连接由首次 Agent 构图 / 前端 probe 触发（懒连接）。
            await mcp_runtime.initialize()
            mcp_runtime.set_structure_handler(self._on_mcp_structure_change)

            store, checkpointer = await create_async_persistence()
            manager = None
            try:
                manager = SandboxManager(store)
                await manager.initialize()
                reader = ThreadHistoryReader(checkpointer=checkpointer)
            except BaseException:
                # 构图失败或初始化取消时，不把半初始化资源暴露给其他请求。
                try:
                    if manager is not None:
                        await manager.close()
                finally:
                    await close_async_persistence(store, checkpointer)
                raise
            self._store, self._checkpointer = store, checkpointer
            self._sandbox_manager = manager
            self._agent_factory = create_main_agent
            self._history_reader = reader
            self._initialized = True

            # 文件监听：mcp_servers.yaml 变化后只重载配置，不主动连接。
            # 工具名集合变化时清空图缓存，让下一次请求按新工具集重建。
            self._mcp_watch_task = asyncio.create_task(
                mcp_runtime.watch_config(),
                name="mcp-config-watch",
            )

    @property
    def store(self) -> Any | None:
        """公开 Store 只读入口，供轨迹/经验/演进 API 使用。"""
        return self._store

    async def _on_mcp_structure_change(self) -> None:
        """MCP 工具名集合增删后，丢弃已缓存的 Agent 图。"""
        logger.info("MCP 工具集结构变化，将按新工具集重建 Agent 图")
        await self.reset_agents()

    @asynccontextmanager
    async def thread_operation(
        self, thread_id: str, *, defer: bool = False,
    ) -> AsyncIterator[bool]:
        """协调单进程内的会话写入；冲突时拒绝请求或让异步投递稍后重试。"""
        # 检查和登记之间没有 await，同一事件循环内不会让两个操作同时进入。
        if thread_id in self._active_threads:
            if defer:
                yield False
                return
            raise HTTPException(status_code=409, detail="会话正在处理中，请稍后重试")
        self._active_threads.add(thread_id)
        try:
            yield True
        finally:
            self._active_threads.discard(thread_id)

    async def require_session(
        self,
        user_id: str,
        thread_id: str,
        *,
        allow_archived: bool = False,
    ) -> dict[str, Any]:
        """在访问已指定的会话前检查归属。

        默认拒绝已归档会话，保证归档后 Agent 不再读取或写入该会话；
        只读查看、恢复、永久删除等管理操作需显式传入 ``allow_archived``。
        """
        session = await self.get_session(user_id, thread_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        if session.get("archived") and not allow_archived:
            raise HTTPException(
                status_code=409,
                detail="会话已归档，请先在「归档会话」中恢复后再继续对话",
            )
        return session

    def create_config(
        self,
        thread_id: str | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> dict[str, dict[str, str]]:
        """创建一次 Agent 调用所需的身份和会话配置。"""
        resolved_user_id = user_id or "anonymous"
        return {
            "configurable": {
                "thread_id": thread_id or str(uuid.uuid4()),
                "user_id": resolved_user_id,
                "username": username or resolved_user_id,
            }
        }

    async def get_agent_for_user(
        self,
        user_id: str,
        username: str,
        thread_id: str,
    ) -> Any:
        """获取用户 Agent，并登记该用户访问过的会话。"""
        await self.initialize()
        assert self._agent_factory is not None
        assert self._store is not None
        assert self._checkpointer is not None
        assert self._sandbox_manager is not None

        # 会话可绑定一个用户自有后端的 MCP；该会话的 Agent 只加载该后端的
        # 集成工具，不加载其他集成后端，避免跨后端误调用业务工具。
        session = await self.get_session(user_id, thread_id)
        integration_server_id = str(
            (session or {}).get("integration_server_id") or ""
        )

        # 用户级锁防止重复构建；沙箱探测和构图不会阻塞其他用户。
        async with self._user_locks.setdefault(user_id, asyncio.Lock()):
            group = self._user_groups.get(user_id)
            if group is None:
                group = UserGroup(username=username)
                self._user_groups[user_id] = group
            else:
                group.username = username
            group.thread_ids.add(thread_id)

            # 每次请求都探测已持久化的沙箱；管理器会对失效实例创建替代品，
            # 并通过稳定代理让已缓存的 Agent 自动使用新后端。
            # 未配置沙箱凭据时，Agent 只能读写项目文件，不能执行命令。
            sandbox_backend = None
            if agent_config.OPEN_SANDBOX_API_KEY:
                sandbox_backend = await self._sandbox_manager.get_backend(user_id)
            else:
                logger.info(
                    "用户 %s 未配置沙箱，Agent 仅可读写项目文件（不能执行命令）",
                    user_id,
                )

            agent = group.agents.get(integration_server_id)
            if agent is None:
                agent = await self._agent_factory(
                    self.create_config(
                        thread_id=thread_id,
                        user_id=user_id,
                        username=username,
                    ),
                    store=self._store,
                    checkpointer=self._checkpointer,
                    sandbox_backend=sandbox_backend,
                    integration_server_id=integration_server_id or None,
                )
                group.agents[integration_server_id] = agent
            return agent

    async def reset_agents(self) -> None:
        """清空用户级 Agent 缓存；模型热更新后，下次请求用新模型重建。"""
        async with self._lock:
            self._user_groups.clear()
            self._user_locks.clear()

    async def reload_sandbox(self) -> None:
        """系统设置保存沙箱配置后重建沙箱管理器与 Agent 图。

        旧管理器会排空正在执行的操作并关闭本地客户端，但不会销毁远端沙箱；
        新配置在下次请求时按需创建/连接。
        """
        await self.initialize()
        async with self._lock:
            previous = self._sandbox_manager
            store = self._store
            self._sandbox_manager = SandboxManager(store) if store is not None else None
            manager = self._sandbox_manager
            self._user_groups.clear()
            self._user_locks.clear()
        if previous is not None:
            await previous.close()
        if manager is not None:
            await manager.initialize()

    def sandbox_status(self) -> dict[str, Any]:
        """返回当前执行环境模式，供前端提示“沙箱 / 本地运行”。"""
        configured = bool(agent_config.OPEN_SANDBOX_API_KEY)
        if configured:
            notice = ""
        else:
            notice = (
                "未配置沙箱服务，当前为项目工作区模式："
                "Agent 只能读写项目文件，不能执行命令。"
            )
        if agent_config.USER_DATA_FALLBACK:
            notice += (
                f" 默认数据目录不可写，配置与索引已改写到 "
                f"{agent_config.USER_DATA_DIR}。"
            )
        return {
            "configured": configured,
            "mode": "sandbox" if configured else "local",
            "notice": notice,
            "workspace_root": str(agent_config.USER_DATA_DIR),
            "user_data_fallback": agent_config.USER_DATA_FALLBACK,
        }

    async def shutdown(self) -> None:
        """释放当前进程内的 Agent 缓存和数据库连接。

        此操作不会删除 PostgreSQL 中任何 checkpoint 或 memories；
        服务下次启动后仍可使用相同的 user_id 和 thread_id 恢复持久化会话。
        """
        async with self._lock:
            watch_task = self._mcp_watch_task
            self._mcp_watch_task = None
            self._user_groups.clear()
            self._agent_factory = None
            self._initialized = False
            store = self._store
            checkpointer = self._checkpointer
            self._store = None
            self._checkpointer = None
            self._history_reader = None
            manager = self._sandbox_manager
            self._sandbox_manager = None
            self._user_locks.clear()
            if watch_task is not None:
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
            try:
                if manager is not None:
                    await manager.close()
            finally:
                from agent.mcp.runtime import mcp_runtime

                await mcp_runtime.shutdown()
                if store is not None and checkpointer is not None:
                    await close_async_persistence(store, checkpointer)

    async def save_session(
        self,
        user_id: str,
        thread_id: str,
        title: str,
        *,
        project_id: str | None = None,
        bind_project: bool = False,
        integration_server_id: str | None = None,
        bind_integration: bool = False,
    ) -> None:
        """将用户会话索引保存到 PostgreSQL 的 LangGraph Store。

        Store 使用 ``("sessions", user_id)`` 作为命名空间、``thread_id`` 作为 key，
        value 保存标题和创建/更新时间；完整对话消息仍由 PostgreSQL Checkpointer 保存。

        ``bind_project`` / ``bind_integration`` 只在新会话创建时使用：归属在创建时
        确定，之后不可更改。
        """
        await self.initialize()
        assert self._store is not None

        namespace = (*SESSION_NAMESPACE_PREFIX, user_id)
        existing = await self._store.aget(namespace, thread_id)
        now = datetime.now(timezone.utc).isoformat()
        value = dict(existing.value) if existing is not None else {}
        existing_title = value.get("title")
        value.setdefault("pinned", False)
        value.setdefault("archived", False)
        value.setdefault("archived_at", None)
        if bind_project:
            value["project_id"] = project_id or None
        if bind_integration:
            value["integration_server_id"] = integration_server_id or None
        value.update(
            {
                "thread_id": thread_id,
                # 首条用户消息到达后只更新这一次，后续轮次保留标题。
                "title": (
                    title
                    if not existing_title or existing_title == "新会话"
                    else existing_title
                ),
                "created_at": value.get("created_at") or now,
                "updated_at": now,
            }
        )
        await self._store.aput(namespace, thread_id, value, index=False)

    async def list_sessions(
        self, user_id: str, *, archived: bool = False,
    ) -> list[dict[str, Any]]:
        """读取当前用户已登记的会话索引，并按更新时间倒序返回。

        ``archived=False`` 只返回正常会话，``archived=True`` 只返回已归档会话。
        """
        await self.initialize()
        assert self._store is not None
        items = await self._store.asearch(
            (*SESSION_NAMESPACE_PREFIX, user_id),
            limit=1_000,
        )
        sessions = [
            dict(item.value)
            for item in items
            if bool(item.value.get("archived", False)) == archived
        ]
        # 置顶优先；组内按最后消息时间倒序。
        return sorted(
            sessions,
            key=lambda session: (
                bool(session.get("pinned")),
                session.get("updated_at", ""),
            ),
            reverse=True,
        )

    async def update_session(
        self,
        user_id: str,
        thread_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any] | None:
        """更新会话标题 / 置顶 / 归档；只改元数据，不动消息与 updated_at。

        会话的项目归属在创建时固定，不允许后续修改。
        """
        await self.initialize()
        assert self._store is not None
        namespace = (*SESSION_NAMESPACE_PREFIX, user_id)
        item = await self._store.aget(namespace, thread_id)
        if item is None:
            return None
        value = dict(item.value)
        if title is not None:
            value["title"] = title
        if pinned is not None:
            value["pinned"] = bool(pinned)
        if archived is not None:
            value["archived"] = bool(archived)
            value["archived_at"] = (
                datetime.now(timezone.utc).isoformat() if archived else None
            )
        await self._store.aput(namespace, thread_id, value, index=False)
        return value

    async def archive_stale_sessions(self, user_id: str, days: int) -> int:
        """把超过 ``days`` 天没有新记录的会话自动归档，返回本次归档数量。

        置顶会话视为用户明确保留，不参与自动归档；``days <= 0`` 表示关闭。
        """
        if days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        archived_count = 0
        for session in await self.list_sessions(user_id, archived=False):
            if session.get("pinned"):
                continue
            try:
                updated_at = datetime.fromisoformat(str(session.get("updated_at", "")))
            except ValueError:
                continue
            # 兼容历史上未带时区的 ISO 字符串。
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at >= cutoff:
                continue
            await self.update_session(user_id, session["thread_id"], archived=True)
            archived_count += 1
        return archived_count

    async def get_session(
        self, user_id: str, thread_id: str,
    ) -> dict[str, Any] | None:
        """读取当前用户的一条会话索引，用于常量时间的会话所有权校验。"""
        await self.initialize()
        assert self._store is not None
        item = await self._store.aget((*SESSION_NAMESPACE_PREFIX, user_id), thread_id)
        return dict(item.value) if item is not None else None

    async def get_sandbox_backend(self, user_id: str) -> Any:
        """返回该用户当前可用的沙箱后端，供文件抽屉读取沙箱文件。"""
        await self.initialize()
        if self._sandbox_manager is None:
            raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
        return await self._sandbox_manager.get_backend(user_id)

    async def resolve_project(self, user_id: str, thread_id: str) -> Any:
        """解析会话模式：无项目为自由会话，项目不存在时同样按自由会话运行。"""
        from agent import project_store

        await self.initialize()
        item = await self.get_session(user_id, thread_id)
        project_id = str((item or {}).get("project_id") or "").strip() or None
        return await asyncio.to_thread(project_store.resolve_project, user_id, project_id)

    async def list_artifacts(
        self, user_id: str, project_id: str, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """读取项目产物发布记录，按时间倒序返回。"""
        await self.initialize()
        assert self._store is not None
        items = await self._store.asearch(
            ("artifacts", user_id, project_id),
            limit=max(1, min(limit, 1000)),
        )
        records = [dict(item.value) for item in items]
        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return records

    async def get_thread_messages(self, thread_id: str) -> list[Any]:
        """恢复指定线程的 LangGraph 消息状态。"""
        await self.initialize()
        assert self._history_reader is not None
        return await self._history_reader.get_messages(thread_id)

    async def get_thread_state(self, thread_id: str) -> Any:
        """恢复包含待处理节点和中断的会话快照。"""
        await self.initialize()
        assert self._history_reader is not None
        return await self._history_reader.get_state(thread_id)

    async def list_trajectory(
        self, user_id: str, thread_id: str,
    ) -> list[dict[str, Any]]:
        """读取某个会话的轨迹事件，供 /trajectory 接口聚合。"""
        await self.initialize()
        assert self._store is not None
        return await list_trajectory_events(
            self._store,
            user_id=user_id,
            thread_id=thread_id,
        )

    async def get_thread_usage(self, user_id: str, thread_id: str) -> dict[str, Any]:
        """读取会话 Token 统计：逐轮明细 + 累计值（由 TokenUsageMiddleware 写入）。"""
        await self.initialize()
        assert self._store is not None
        return await read_usage(self._store, user_id=user_id, thread_id=thread_id)

    async def bind_async_task(
        self,
        task_id: str,
        *,
        user_id: str,
        username: str,
        thread_id: str,
    ) -> None:
        """持久化远程任务与主会话的归属关系。"""
        await self.initialize()
        assert self._store is not None
        await self._store.aput(
            ASYNC_TASK_NAMESPACE_PREFIX,
            task_id,
            {
                "task_id": task_id,
                "user_id": user_id,
                "username": username,
                "thread_id": thread_id,
            },
            index=False,
        )

    async def get_async_task_binding(
        self, task_id: str,
    ) -> AsyncTaskBinding | None:
        """读取远程任务绑定；旧任务未登记时返回空值。"""
        await self.initialize()
        assert self._store is not None
        item = await self._store.aget(ASYNC_TASK_NAMESPACE_PREFIX, task_id)
        if item is None:
            return None
        value = item.value
        return AsyncTaskBinding(
            task_id=task_id,
            user_id=str(value["user_id"]),
            username=str(value["username"]),
            thread_id=str(value["thread_id"]),
        )

    async def publish_async_task_result(
        self,
        task_id: str,
        *,
        content: str,
    ) -> bool:
        """将终态异步任务结果作为主 Agent 消息幂等写入主会话。"""
        binding = await self.get_async_task_binding(task_id)
        if binding is None:
            return False

        async with self.thread_operation(binding.thread_id, defer=True) as acquired:
            if not acquired or await self.get_session(
                binding.user_id, binding.thread_id,
            ) is None:
                return False
            return await self._publish_bound_result(binding, content=content)

    async def _publish_bound_result(
        self,
        binding: AsyncTaskBinding,
        *,
        content: str,
    ) -> bool:
        """在持有会话 guard 后检查幂等性和暂停状态，再写入结果。"""
        task_id = binding.task_id
        message_id = f"{ASYNC_TASK_MESSAGE_PREFIX}{task_id}"
        # 必须经已编译图读取 DeltaChannel 状态，不能直接读取原始 checkpoint。
        state = await self.get_thread_state(binding.thread_id)
        messages = state.values.get("messages", [])
        message_ids = (
            message.get("id", "") if isinstance(message, dict) else getattr(message, "id", "")
            for message in messages
        )
        if message_id in message_ids:
            return True
        # aupdate_state 会改变 checkpoint 的后续调度，不能覆盖暂停中的工具执行。
        if state.next or state.interrupts:
            return False

        agent = await self.get_agent_for_user(
            user_id=binding.user_id,
            username=binding.username,
            thread_id=binding.thread_id,
        )
        # 使用固定消息 ID 配合 messages reducer，避免多次轮询或重试产生重复消息。
        await agent.aupdate_state(
            self.create_config(
                thread_id=binding.thread_id,
                user_id=binding.user_id,
                username=binding.username,
            ),
            {
                "messages": [
                    AIMessage(
                        id=message_id,
                        content=content,
                        additional_kwargs={
                            "source": "main",
                            "async_task_id": task_id,
                        },
                    )
                ]
            },
        )
        await self.save_session(binding.user_id, binding.thread_id, "新会话")
        return True

    async def delete_session(self, user_id: str, thread_id: str) -> bool:
        """删除用户会话的 checkpoint 数据和会话索引。"""
        async with self.thread_operation(thread_id):
            # 永久删除需要能清理已归档会话。
            await self.require_session(user_id, thread_id, allow_archived=True)
            return await self._delete_session(user_id, thread_id)

    async def _delete_session(self, user_id: str, thread_id: str) -> bool:
        """在持有会话 guard 时删除正文与索引，阻止延迟结果重建已删除会话。"""
        await self.initialize()
        assert self._store is not None
        assert self._checkpointer is not None
        # 先删除正文 checkpoint，再删除索引，避免侧边栏指向不存在的会话。
        await self._checkpointer.adelete_thread(thread_id)
        await self._store.adelete((*SESSION_NAMESPACE_PREFIX, user_id), thread_id)
        # 轨迹与 checkpoint 同生命周期，会话删除后不再保留过程记录。
        await delete_trajectory(self._store, user_id=user_id, thread_id=thread_id)
        await delete_usage(self._store, user_id=user_id, thread_id=thread_id)
        group = self._user_groups.get(user_id)
        if group is not None:
            group.thread_ids.discard(thread_id)
        return True


agent_loader = AgentLoader()
