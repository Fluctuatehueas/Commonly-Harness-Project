"""
支持热替换的稳定沙箱后端引用。
问题背景：Agent 图在构建时（create_deep_agent(backend=...)）会把 backend 对象捕获进图里，后续每个工具调用都通过这个引用访问沙箱。但沙箱是有生命周期的：
沙箱可能崩溃、被回收、被重建。
用户可能切换到另一个沙箱（比如换机器、换区域）。
沙箱的底层 SDK 客户端可能需要重连。
如果 Agent 图直接持有具体沙箱对象，一旦沙箱换掉，图里所有引用都失效，只能重建整个 Agent 图——代价大、状态丢失、正在跑的会话中断。
解决方案：Agent 图持有的是稳定的代理对象，代理内部持有可替换的实际后端。沙箱换了，只替换代理内部引用，图里的引用不变。这就是经典的 Proxy（代理）模式 + 引用稳定性。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

class SandboxBackendProxy(BaseSandbox):
    """把所有 BaseSandbox 原语转发到当前用户沙箱。

    Agent 图在构建时会捕获后端对象。恢复沙箱时替换被代理的后端，而不是重建
    Agent 图，可以保持图中已有引用继续有效。

    并发语义：
    - 普通操作（execute / 文件传输 / 健康检查）可以并发执行，不再被一把锁串行化，
      多个子 Agent 因此能在同一个沙箱里真正并行干活；
    - ``replace_backend`` / ``close`` 需要等所有在途操作和事务结束后再替换，
      避免热替换把正在执行的命令打断。
    """
    def __init__(self, backend: BaseSandbox | None = None) -> None:
       self._backend = backend
       self._condition = Condition()
       self._active_ops = 0
       self._transactions = 0
       self._replacing = False

    #  上下文管理器：事务期间禁止替换后端，但允许普通操作继续并发。
    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._condition:
            while self._replacing:
                self._condition.wait()
            self._transactions += 1
        try:
            yield
        finally:
            with self._condition:
                self._transactions -= 1
                if self._transactions == 0:
                    self._condition.notify_all()

    @contextmanager
    def _operation(self) -> Iterator[BaseSandbox]:
        """登记一次在途操作；替换/关闭会等待它结束。"""
        with self._condition:
            while self._replacing:
                self._condition.wait()
            backend = self._require_backend()
            self._active_ops += 1
        try:
            yield backend
        finally:
            with self._condition:
                self._active_ops -= 1
                if self._active_ops == 0:
                    self._condition.notify_all()

    def _wait_idle(self) -> BaseSandbox | None:
        """进入替换流程并等待在途操作清空；调用方需持有 condition。"""
        self._replacing = True
        while self._active_ops or self._transactions:
            self._condition.wait()
        previous = self._backend
        return previous

    # 请求一个沙箱
    def _require_backend(self) -> BaseSandbox:
        """
        拒绝在已关闭或仅用于观察的代理上执行操作。
        """
        if self._backend is None:
            raise RuntimeError("Sandbox is unavailable outside an execution context")
        return self._backend
    
    @property
    def id(self) -> str:
        """返回当前沙箱标识"""
        with self._operation() as backend:
            return backend.id
    
    # 热替换的核心
    def replace_backend(self, backend: BaseSandbox) -> None:
        """
        等待进行中的操作完成，安装新后端并关闭旧客户端。
        """
        with self._condition:
            previous = self._wait_idle()
            self._backend = backend
            self._replacing = False
            self._condition.notify_all()
        if previous is not None and previous is not backend:
            close = getattr(previous, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        """
        等待活动操作完成，并只关闭一次本地资源。
        """
        with self._condition:
            previous = self._wait_idle()
            self._backend = None
            self._replacing = False
            self._condition.notify_all()
        if previous is not None:
            close = getattr(previous, "close", None)
            if callable(close):
                close()

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """将命令执行转发到当前沙箱（支持多子 Agent 并发）。"""
        with self._operation() as backend:
            return backend.execute(command, timeout=timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """将文件上传转发到当前沙箱。"""
        with self._operation() as backend:
            return backend.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """将文件下载转发到当前沙箱。"""
        with self._operation() as backend:
            return backend.download_files(paths)

    # 健康指针
    def is_healthy(self) -> bool:
        """使用当前后端的健康探针返回沙箱状态。"""
        try:
            with self._operation() as backend:
                probe = getattr(backend, "is_healthy", None)
                if callable(probe):
                    return bool(probe())
                # 不要嵌套调用 self.execute：替换流程等待在途操作时会死锁。
                return backend.execute("true").exit_code == 0
        except Exception:
            return False
