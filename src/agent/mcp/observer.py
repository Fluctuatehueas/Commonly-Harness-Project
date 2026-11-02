"""MCP 观测层：结构化日志、内存日志缓冲与基础计数。"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class McpObserver:
    """不依赖外部监控系统的轻量观测器。"""

    def __init__(self, max_events_per_server: int = 300) -> None:
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_events_per_server)
        )
        self._global: deque[dict[str, Any]] = deque(maxlen=500)
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)

    def record(
        self,
        event: str,
        *,
        server_id: str = "",
        level: str = "info",
        **fields: Any,
    ) -> dict[str, Any]:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "server_id": server_id,
            "level": level,
            **fields,
        }
        with self._lock:
            if server_id:
                self._events[server_id].append(payload)
            self._global.append(payload)
            self._counters[event] += 1
        log = getattr(logger, level if level in {"debug", "info", "warning", "error"} else "info")
        log("%s server=%s %s", event, server_id, fields)
        return payload

    def log_line(
        self,
        server_id: str,
        message: str,
        *,
        level: str = "info",
    ) -> None:
        """给前端「最近日志」提供可读行。"""
        self.record(
            "mcp.log",
            server_id=server_id,
            level=level,
            message=message,
            ts_ms=int(time.time() * 1000),
        )

    def recent(self, server_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._events.get(server_id, ()))
        return items[-limit:]

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def global_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._global)[-limit:]
