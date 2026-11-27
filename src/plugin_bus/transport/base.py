"""插件传输抽象。

三种传输（stdio / http / sandbox_stdio）实现同一组方法，Runtime 不感知差异。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable

HostHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class Transport(ABC):
    """一条插件连接的抽象。

    ``host_handler`` 处理插件反向发起的 ``host.*`` 请求；不支持的传输可以忽略。
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        version: str,
        host_handler: HostHandler | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.version = version
        self.host_handler = host_handler
        self.log_path = log_path

    @abstractmethod
    async def open(self) -> None:
        """建立连接或完成沙箱准备。"""

    @abstractmethod
    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> Any:
        """发起一次请求并等待结果。"""

    @abstractmethod
    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """发送通知，不等待结果。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接、回收进程或资源。"""

    @property
    def connected(self) -> bool:
        return True


__all__ = ["Transport", "HostHandler"]
