"""MCP 客户端懒连接与连接池。

AnyIO 的 cancel scope 必须在创建它的 task 内退出，因此这里采用
「每个物理连接一个常驻 worker task」的模型：

- ``acquire(config)`` 才启动 worker，worker 内部建立会话并等待请求；
- 工具发现 / 调用 / ping / 关闭都通过 worker 的消息队列执行；
- 连接按 ``pool_key``（连接签名）复用；
- 引用计数归零后等待 ``idle_ttl_ms`` 再由 worker 自行关闭；
- 同一 pool key 的并发建连只执行一次（单飞）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from langchain_mcp_adapters.client import create_session
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

from agent.config import mcp_stdio_log_path
from agent.mcp.types import ErrorCategory, McpError, PoolPolicy, ServerConfig, Transport

logger = logging.getLogger(__name__)

# 子进程日志回调：(server_id, message, level)
LogSink = Callable[[str, str, str], None]

# 当前正在建立/持有连接的 server_id；stdio 客户端在自己的 task 里读 stdout
# 报错时，靠它把日志归到正确的服务上（子 task 会继承创建时的 context）。
_current_server: ContextVar[str] = ContextVar("mcp_current_server", default="")

_TAIL_INTERVAL_SECONDS = 0.4
_LOG_LINE_MAX = 500
_MAX_LINES_PER_CYCLE = 50


def _guess_level(line: str) -> str:
    """按内容粗分日志级别，让前端能用颜色区分。"""
    text = line.lower()
    if "error" in text or "traceback" in text or "eperm" in text or "exception" in text:
        return "error"
    if "warn" in text or "notice" in text:
        return "warning"
    return "info"


class _StdioParseErrorHandler(logging.Handler):
    """把 stdio 子进程写到 stdout 的非 JSON-RPC 行收集到观测层。

    mcp SDK 在 stdout 解析失败时只打印一条堆栈；原始行藏在 pydantic
    校验错误里，这里尽量取出来，避免用户只看到“连接被关闭”。
    """

    def __init__(self, sink: LogSink) -> None:
        super().__init__(level=logging.WARNING)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        server_id = _current_server.get()
        if not server_id:
            return
        message = "子进程 stdout 输出了非 JSON-RPC 内容，该行已被丢弃"
        error = record.exc_info[1] if record.exc_info else None
        if error is not None:
            match = re.search(
                r"input_value=(.+?)(?:,\s*input_type|$)",
                str(error),
                re.S,
            )
            if match:
                message = f"{message}：{match.group(1).strip()[:_LOG_LINE_MAX]}"
        self._sink(server_id, message, "warning")


def _emit_new_lines(
    state: dict[str, int],
    path_str: str,
    server_id: str,
    sink: LogSink,
) -> None:
    """把日志文件里尚未上报的增量行写入观测层（同步实现，供 tail 与收尾复用）。"""
    try:
        size = os.stat(path_str).st_size
    except OSError:
        return
    start = state.get("pos", 0)
    if size <= start:
        return
    try:
        with open(path_str, "rb") as handle:
            handle.seek(start)
            chunk = handle.read(size - start)
    except OSError:
        return
    state["pos"] = start + len(chunk)
    lines = [
        line.strip()[:_LOG_LINE_MAX]
        for line in chunk.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    if len(lines) > _MAX_LINES_PER_CYCLE:
        sink(
            server_id,
            f"（子进程输出过快，省略前 {len(lines) - _MAX_LINES_PER_CYCLE} 行）",
            "info",
        )
        lines = lines[-_MAX_LINES_PER_CYCLE:]
    for text in lines:
        sink(server_id, text, _guess_level(text))


async def _tail_log(
    path_str: str,
    server_id: str,
    sink: LogSink,
    stop: asyncio.Event,
    state: dict[str, int],
) -> None:
    """增量读取子进程 stderr 文件，写入观测层。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TAIL_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        await asyncio.to_thread(_emit_new_lines, state, path_str, server_id, sink)


@asynccontextmanager
async def _stdio_session(
    config: ServerConfig,
    sink: LogSink | None,
) -> AsyncIterator[ClientSession]:
    """建立 stdio 会话，并把子进程 stderr 落盘 + 实时转发到观测层。"""
    log_path = mcp_stdio_log_path(config.server_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    errlog = log_path.open("w", encoding="utf-8", errors="replace")
    stop = asyncio.Event()
    state: dict[str, int] = {"pos": 0}
    tail_task = (
        asyncio.create_task(
            _tail_log(str(log_path), config.server_id, sink, stop, state),
            name=f"mcp-stdio-log-{config.server_id}",
        )
        if sink is not None
        else None
    )
    try:
        params = StdioServerParameters(
            command=str(config.connection.get("command") or ""),
            args=list(config.connection.get("args") or []),
            env=dict(config.connection.get("env") or {}) or None,
            cwd=config.connection.get("cwd") or None,
            encoding=str(config.connection.get("encoding") or "utf-8"),
            encoding_error_handler=str(
                config.connection.get("encoding_error_handler") or "strict"
            ),
        )
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    finally:
        stop.set()
        if tail_task is not None:
            tail_task.cancel()
            with contextlib.suppress(BaseException):
                await tail_task
        errlog.close()
        if sink is not None:
            # 进程已经结束，补一次收尾读取：快速失败的场景不会等到下一个轮询点。
            await asyncio.to_thread(
                _emit_new_lines, state, str(log_path), config.server_id, sink
            )


@dataclass
class _PoolRequest:
    """发往会话 worker 的一次操作。"""

    kind: str  # call | list_tools | ping | close
    future: asyncio.Future[Any]
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 0


@dataclass
class PooledSession:
    """连接池中的一个物理会话及其 worker task。"""

    pool_key: str
    server_id: str
    requests: asyncio.Queue[_PoolRequest]
    ready: asyncio.Future[None]
    worker: asyncio.Task[None]
    session: ClientSession | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    ref_count: int = 0
    closed: bool = False
    connect_ms: int = 0
    reused: bool = False

    def touch(self) -> None:
        self.last_used = time.monotonic()


class MCPClientRegistry:
    """按连接签名复用 MCP 会话的进程级连接池。"""

    def __init__(
        self,
        policy: PoolPolicy | None = None,
        *,
        log_sink: LogSink | None = None,
    ) -> None:
        self.policy = policy or PoolPolicy()
        self._log_sink = log_sink
        self._entries: dict[str, PooledSession] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._log_handler: logging.Handler | None = None
        self._closed = False
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def size(self) -> int:
        return len(self._entries)

    def stats(self) -> dict[str, Any]:
        return {
            "size": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "idle_ttl_ms": self.policy.idle_ttl_ms,
            "max_pool_size": self.policy.max_pool_size,
        }

    async def _key_lock(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    # ---------- worker ----------

    async def _worker(
        self,
        entry: PooledSession,
        config: ServerConfig,
    ) -> None:
        """在独立 task 中持有会话、执行请求、最后关闭会话。"""
        stack = AsyncExitStack()
        started = time.monotonic()
        token = _current_server.set(config.server_id)
        try:
            if config.transport is Transport.STDIO:
                session = await asyncio.wait_for(
                    stack.enter_async_context(
                        _stdio_session(config, self._log_sink)
                    ),
                    timeout=config.connect_timeout_ms / 1000,
                )
            else:
                session = await asyncio.wait_for(
                    stack.enter_async_context(create_session(config.connection)),
                    timeout=config.connect_timeout_ms / 1000,
                )
            await asyncio.wait_for(
                session.initialize(),
                timeout=config.connect_timeout_ms / 1000,
            )
            entry.session = session
            entry.connect_ms = int((time.monotonic() - started) * 1000)
            if not entry.ready.done():
                entry.ready.set_result(None)
            logger.info(
                "MCP 懒连接成功: server=%s pool_key=%s connect_ms=%s",
                config.server_id,
                entry.pool_key[:23],
                entry.connect_ms,
            )
            while True:
                request = await entry.requests.get()
                if request.kind == "close":
                    if not request.future.done():
                        request.future.set_result(None)
                    break
                try:
                    timeout = (
                        request.timeout_ms / 1000
                        if request.timeout_ms > 0
                        else None
                    )
                    if request.kind == "call":
                        result = await asyncio.wait_for(
                            session.call_tool(
                                request.tool,
                                request.args,
                            ),
                            timeout=timeout,
                        )
                        if not request.future.done():
                            request.future.set_result(result)
                    elif request.kind == "list_tools":
                        result = await asyncio.wait_for(
                            session.list_tools(),
                            timeout=timeout,
                        )
                        if not request.future.done():
                            request.future.set_result(result)
                    elif request.kind == "ping":
                        await asyncio.wait_for(
                            session.send_ping(),
                            timeout=timeout,
                        )
                        if not request.future.done():
                            request.future.set_result(None)
                    else:
                        if not request.future.done():
                            request.future.set_exception(
                                RuntimeError(f"未知 MCP 会话操作: {request.kind}")
                            )
                except BaseException as exc:  # noqa: BLE001
                    if not request.future.done():
                        request.future.set_exception(exc)
        except BaseException as exc:  # noqa: BLE001
            if not entry.ready.done():
                entry.ready.set_exception(exc)
            logger.debug("MCP worker 异常退出: %s", exc, exc_info=True)
        finally:
            entry.closed = True
            try:
                await stack.aclose()
            except BaseException:
                logger.debug("关闭 MCP 会话时出现异常", exc_info=True)
            _current_server.reset(token)
            # 失败未完成的请求统一唤醒，避免调用方永久挂起
            while not entry.requests.empty():
                pending = entry.requests.get_nowait()
                if not pending.future.done():
                    pending.future.set_exception(
                        McpError(
                            "MCP 会话已关闭",
                            code="MCP_SESSION_CLOSED",
                            category=ErrorCategory.TRANSIENT,
                            server_id=entry.server_id,
                            retryable=True,
                        )
                    )

    # ---------- 获取/释放 ----------

    async def acquire(self, config: ServerConfig) -> PooledSession:
        """获取会话；不存在或已关闭则启动一个新 worker。"""
        if self._closed:
            raise McpError(
                "MCP 连接池已关闭",
                code="MCP_POOL_CLOSED",
                category=ErrorCategory.PERMANENT,
                server_id=config.server_id,
            )
        key = config.pool_key
        lock = await self._key_lock(key)
        async with lock:
            entry = self._entries.get(key)
            if entry is not None and not entry.closed:
                entry.ref_count += 1
                entry.touch()
                entry.reused = True
                self._hits += 1
                return entry

            if entry is not None and entry.closed:
                self._entries.pop(key, None)

            loop = asyncio.get_running_loop()
            entry = PooledSession(
                pool_key=key,
                server_id=config.server_id,
                requests=asyncio.Queue(),
                ready=loop.create_future(),
                worker=None,  # type: ignore[arg-type]
            )
            entry.worker = asyncio.create_task(
                self._worker(entry, config),
                name=f"mcp-session-{config.server_id}",
            )
            self._entries[key] = entry
            try:
                await asyncio.wait_for(
                    entry.ready,
                    timeout=config.connect_timeout_ms / 1000,
                )
            except BaseException:
                self._entries.pop(key, None)
                await self._close_entry(entry)
                self._misses += 1
                raise
            entry.ref_count = 1
            entry.reused = False
            self._misses += 1
            await self._enforce_max_size()
            return entry

    async def release(self, entry: PooledSession) -> None:
        async with self._global_lock:
            entry.ref_count = max(0, entry.ref_count - 1)
            entry.touch()

    def peek(self, config: ServerConfig) -> PooledSession | None:
        """查看池中是否已有该连接签名的会话（不增加引用计数）。"""
        entry = self._entries.get(config.pool_key)
        if entry is None or entry.closed:
            return None
        return entry

    # ---------- 请求 ----------

    @staticmethod
    def _ensure_open(entry: PooledSession) -> None:
        if entry.closed or entry.worker.done():
            raise McpError(
                "MCP 会话已关闭",
                code="MCP_SESSION_CLOSED",
                category=ErrorCategory.TRANSIENT,
                server_id=entry.server_id,
                retryable=True,
            )

    async def _request(
        self,
        entry: PooledSession,
        kind: str,
        *,
        tool: str = "",
        args: dict[str, Any] | None = None,
        timeout_ms: int = 120000,
    ) -> Any:
        self._ensure_open(entry)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await entry.requests.put(
            _PoolRequest(
                kind=kind,
                future=future,
                tool=tool,
                args=dict(args or {}),
                timeout_ms=timeout_ms,
            )
        )
        return await asyncio.wait_for(future, timeout=timeout_ms / 1000)

    async def call_tool(
        self,
        entry: PooledSession,
        tool: str,
        args: dict[str, Any],
        *,
        timeout_ms: int,
    ) -> Any:
        return await self._request(
            entry,
            "call",
            tool=tool,
            args=args,
            timeout_ms=timeout_ms,
        )

    async def list_tools(self, entry: PooledSession, *, timeout_ms: int) -> Any:
        return await self._request(entry, "list_tools", timeout_ms=timeout_ms)

    async def ping(self, entry: PooledSession, *, timeout_ms: int) -> None:
        await self._request(entry, "ping", timeout_ms=timeout_ms)

    # ---------- 关闭/回收 ----------

    async def _close_entry(self, entry: PooledSession) -> None:
        if entry.closed and entry.worker.done():
            return
        if not entry.worker.done():
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Any] = loop.create_future()
            await entry.requests.put(
                _PoolRequest(kind="close", future=future)
            )
            try:
                await asyncio.wait_for(future, timeout=5)
                await asyncio.wait_for(entry.worker, timeout=5)
            except BaseException:
                entry.worker.cancel()
                try:
                    await entry.worker
                except BaseException:
                    pass
        entry.closed = True

    async def close_key(self, pool_key: str) -> None:
        async with self._global_lock:
            entry = self._entries.pop(pool_key, None)
        if entry is not None:
            await self._close_entry(entry)

    async def close_server(self, config: ServerConfig) -> None:
        await self.close_key(config.pool_key)

    async def _enforce_max_size(self) -> None:
        if len(self._entries) <= self.policy.max_pool_size:
            return
        candidates = sorted(
            (entry for entry in self._entries.values() if entry.ref_count == 0),
            key=lambda entry: entry.last_used,
        )
        for entry in candidates[: len(self._entries) - self.policy.max_pool_size]:
            async with self._global_lock:
                if self._entries.get(entry.pool_key) is entry:
                    self._entries.pop(entry.pool_key, None)
            await self._close_entry(entry)
            self._evictions += 1

    async def _reap_loop(self) -> None:
        interval = max(self.policy.idle_ttl_ms / 1000 / 2, 10)
        while not self._closed:
            await asyncio.sleep(interval)
            await self.evict_idle()

    async def evict_idle(self) -> int:
        """回收空闲超过 TTL 的会话，返回回收数量。"""
        now = time.monotonic()
        expired: list[PooledSession] = []
        async with self._global_lock:
            for key, entry in list(self._entries.items()):
                if entry.ref_count > 0 or entry.closed:
                    continue
                if now - entry.last_used >= self.policy.idle_ttl_ms / 1000:
                    self._entries.pop(key, None)
                    expired.append(entry)
        for entry in expired:
            await self._close_entry(entry)
            self._evictions += 1
            logger.info(
                "MCP 空闲连接回收: server=%s pool_key=%s",
                entry.server_id,
                entry.pool_key[:23],
            )
        return len(expired)

    def start(self) -> None:
        """启动空闲回收任务；进程内重新初始化时也允许重新打开池。"""
        self._closed = False
        self._install_log_handler()
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(
                self._reap_loop(), name="mcp-pool-reaper"
            )

    def _install_log_handler(self) -> None:
        """挂一次性 handler，把 stdio 解析错误也记到对应服务的日志里。"""
        if self._log_handler is not None or self._log_sink is None:
            return
        handler = _StdioParseErrorHandler(self._log_sink)
        logging.getLogger("mcp.client.stdio").addHandler(handler)
        self._log_handler = handler

    async def shutdown(self) -> None:
        self._closed = True
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        async with self._global_lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await self._close_entry(entry)
        logger.info("MCP 连接池已关闭，共释放 %d 个会话", len(entries))
