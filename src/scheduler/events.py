"""调度后台运行的事件总线。

调度工具把执行放到后台后，本轮对话的 LangGraph StreamWriter 会随请求结束失效，
后台任务不能再通过它把进度写进聊天 SSE。本模块提供进程内、按 ``run_id``
隔离的广播通道：

- 事件先写历史再广播，晚到的订阅者可以完整回放；
- ``run_finished`` 之后保留历史，供重连或刷新后的订阅者读取终态；
- 只支持单进程部署；进程重启后的未完成运行由启动清理标记为中断。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

# 单次运行最多保留的事件数；足够覆盖一个计划的条目与检查点事件。
MAX_HISTORY = 500
# 进程内同时保留的运行通道上限，避免异常场景下无界增长。
MAX_CHANNELS = 64
# 终态通道在容量压力下的最短保留时间，避免刚结束就被淘汰。
TERMINAL_GRACE_SECONDS = 60.0


@dataclass
class _Channel:
    seq: int = 0
    history: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY)
    )
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    terminal_at: float | None = None


class SchedulerEventBus:
    """按 run_id 广播调度事件，并保留可回放的历史。"""

    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}

    def publish(
        self, run_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """写入历史并广播；run_id 为空时直接忽略。"""
        if not run_id:
            return None
        self._evict_if_needed()
        channel = self._channels.setdefault(run_id, _Channel())
        channel.seq += 1
        event = {
            "type": "scheduler_progress",
            **payload,
            "run_id": run_id,
            "seq": channel.seq,
        }
        channel.history.append(event)
        if event.get("event") == "run_finished":
            channel.terminal_at = time.monotonic()
        for queue in list(channel.subscribers):
            queue.put_nowait(event)
        return event

    def open(
        self, run_id: str, after_seq: int = 0
    ) -> tuple[
        list[dict[str, Any]],
        asyncio.Queue[dict[str, Any]],
        Callable[[], None],
    ]:
        """打开订阅：返回历史快照、实时队列和关闭函数。

        先登记订阅者再复制历史，保证登记与回放之间产生的新事件不会丢失；
        两者可能重复，调用方需要按 ``seq`` 去重。
        """
        channel = self._channels.setdefault(run_id, _Channel())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        channel.subscribers.add(queue)
        history = [
            event
            for event in channel.history
            if int(event.get("seq") or 0) > after_seq
        ]

        def close() -> None:
            channel.subscribers.discard(queue)

        return history, queue, close

    def snapshot(self, run_id: str, event_name: str) -> dict[str, Any] | None:
        """读取指定类型的首条历史事件，供数据库不可用时兜底。"""
        channel = self._channels.get(run_id)
        if channel is None:
            return None
        for event in channel.history:
            if event.get("event") == event_name:
                return event
        return None

    def has(self, run_id: str) -> bool:
        return run_id in self._channels

    def _evict_if_needed(self) -> None:
        if len(self._channels) <= MAX_CHANNELS:
            return
        now = time.monotonic()
        removable = [
            run_id
            for run_id, channel in self._channels.items()
            if not channel.subscribers
            and channel.terminal_at is not None
            and now - channel.terminal_at > TERMINAL_GRACE_SECONDS
        ]
        for run_id in removable[: len(self._channels) - MAX_CHANNELS]:
            self._channels.pop(run_id, None)


scheduler_events = SchedulerEventBus()

__all__ = ["SchedulerEventBus", "scheduler_events"]
