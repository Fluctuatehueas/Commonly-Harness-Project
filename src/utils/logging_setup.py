"""日志初始化：结构化文件 + 人类可读控制台 + 异步队列。

设计要点（详见 docs/develope/日志设计开发.md）：

- **非阻塞**：业务线程只写内存队列，磁盘 I/O 在 ``QueueListener`` 线程完成，
  不会拖慢 Agent 首 token；
- **可关联**：``trace_id`` / ``run_id`` / ``thread_id`` 等由 ``log_context`` 提供，
  过滤器统一补齐，业务代码不重复传；
- **分文件**：``app.log``（INFO+）、``error.log``（ERROR+，含堆栈）、``audit.log``
  （审批与安全事件白名单）；
- **默认脱敏**：密钥、令牌、连接串口令在格式化阶段统一抹除，避免漏点。

文件一律 JSON Lines（便于 grep / jq），控制台保持人类可读。
"""

from __future__ import annotations

import copy
import json
import logging
import logging.handlers
import os
import re
from datetime import datetime
from pathlib import Path
from queue import SimpleQueue
from typing import Any, Iterable

from utils.log_context import current_log_fields

# 审计 logger 名称：只有显式使用它的调用点才会进 audit.log。
AUDIT_LOGGER = "harness.audit"

# 日志里的公共字段；其余 extra 一律当作结构化附加字段输出。
_STANDARD_KEYS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)

# 需要抹除的敏感模式；顺序从具体到宽泛，避免被前面的规则吃掉。
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{6,})"), "sk-***"),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|passwd|secret)\s*[:=]\s*)"
                r"['\"]?[^\s'\",;]{3,}"), r"\1***"),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)"), r"\1***\2"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
)

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_DEFAULT_BACKUPS = 10
_ERROR_MAX_BYTES = 20 * 1024 * 1024


def redact(text: str) -> str:
    """抹除常见凭据模式；日志格式化统一走这里，调用方不需要各自处理。"""
    if not text:
        return text
    result = text
    for pattern, replacement in _REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """取出调用方通过 ``extra=`` 传入的结构化字段。"""
    extras: dict[str, Any] = {}
    for key, value in vars(record).items():
        if key in _STANDARD_KEYS or key.startswith("_"):
            continue
        extras[key] = value
    return extras


class ContextQueueHandler(logging.handlers.QueueHandler):
    """入队前把关联字段写进 record，并保留异常堆栈。

    两个必须自己处理的点：

    1. 关联字段要在**调用线程**补齐：``QueueListener`` 在自己的线程消费队列，
       那里读不到请求协程的 contextvars，放在 handler 的 filter 里会失效。
    2. 父类 ``prepare`` 会清空 ``exc_info``（为了可 pickle），但这里是进程内
       ``SimpleQueue``，不清空才能让 ``error.log`` 与终端各自格式化堆栈。
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        for key, value in current_log_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return copy.copy(record)


class ContextFilter(logging.Filter):
    """兜底过滤器：handler 直接使用时也能补上关联字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_log_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """一行一个 JSON；异常堆栈只进 error 级别，且统一脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()[:500]),
        }
        extras = _extras(record)
        event = extras.pop("event", None)
        if event:
            payload["event"] = str(event)
        for key, value in extras.items():
            payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["stack"] = redact(self.formatException(record.exc_info)[:8000])
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    """控制台格式：时间 | 级别 | 模块 | 关联 ID | 消息。"""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", "") or "-"
        run_id = getattr(record, "run_id", "") or "-"
        stamp = datetime.fromtimestamp(record.created).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S,%f"
        )[:-3]
        line = (
            f"{stamp} | {record.levelname:<8} | {record.name} | "
            f"trace={trace_id} run={run_id} | {redact(record.getMessage())}"
        )
        if record.exc_info:
            line = f"{line}\n{redact(self.formatException(record.exc_info))}"
        return line


def configure_logging() -> logging.handlers.QueueListener | None:
    """初始化日志管道；重复调用会先清理旧 handler。

    Returns:
        队列监听器；调用方应在进程退出时 ``stop()`` 以保证缓冲落盘。
    """
    from utils.log_context import clear_log_fields

    level_name = os.getenv("HARNESS_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = Path(os.getenv("HARNESS_LOG_DIR", "logs"))
    json_mode = _env_flag("HARNESS_LOG_JSON", True)
    max_bytes = int(os.getenv("HARNESS_LOG_MAX_BYTES", str(_DEFAULT_MAX_BYTES)))
    backups = int(os.getenv("HARNESS_LOG_BACKUPS", str(_DEFAULT_BACKUPS)))
    error_backups = int(os.getenv("HARNESS_LOG_ERROR_BACKUPS", str(_DEFAULT_BACKUPS)))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # 具体级别交给各 handler 过滤
    for handler in list(root.handlers):
        root.removeHandler(handler)

    queue: SimpleQueue = SimpleQueue()
    root.addHandler(ContextQueueHandler(queue))

    handlers: list[logging.Handler] = []

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(HumanFormatter())
    console.addFilter(ContextFilter())
    handlers.append(console)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        app_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
        )
        app_handler.setLevel(level)
        app_handler.setFormatter(JsonFormatter() if json_mode else HumanFormatter())
        app_handler.addFilter(ContextFilter())
        handlers.append(app_handler)

        error_handler = logging.handlers.RotatingFileHandler(
            log_dir / "error.log",
            maxBytes=_ERROR_MAX_BYTES,
            backupCount=error_backups,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter())
        error_handler.addFilter(ContextFilter())
        handlers.append(error_handler)
    except OSError:
        # 目录不可写（只读部署）时降级为仅控制台，不影响服务启动。
        logging.getLogger(__name__).warning("日志目录不可写，仅输出到控制台：%s", log_dir)

    audit_handler: logging.Handler | None = None
    try:
        audit_handler = logging.handlers.RotatingFileHandler(
            log_dir / "audit.log",
            maxBytes=_ERROR_MAX_BYTES,
            backupCount=backups,
            encoding="utf-8",
        )
        audit_handler.setLevel(logging.INFO)
        audit_handler.setFormatter(JsonFormatter())
        audit_handler.addFilter(ContextFilter())
    except OSError:
        audit_handler = None

    listener = logging.handlers.QueueListener(
        queue, *handlers, respect_handler_level=True
    )
    listener.start()

    if audit_handler is not None:
        audit_logger = logging.getLogger(AUDIT_LOGGER)
        audit_logger.addHandler(audit_handler)
        audit_logger.setLevel(logging.INFO)

    clear_log_fields()
    logging.getLogger(__name__).info(
        "event=log.ready level=%s dir=%s json=%s", level_name, log_dir, json_mode
    )
    return listener


def stop_logging(listener: logging.handlers.QueueListener | None) -> None:
    """停止队列监听并刷出缓冲，保证进程退出前日志不丢。"""
    if listener is None:
        return
    try:
        listener.stop()
    except Exception:  # noqa: BLE001 - 退出阶段不再抛出
        pass


def audit(event: str, **fields: Any) -> None:
    """写一条审计日志；只有白名单事件才应该调用本函数。"""
    logging.getLogger(AUDIT_LOGGER).info("%s", event, extra={"event": event, **fields})


__all__: Iterable[str] = (
    "AUDIT_LOGGER",
    "ContextFilter",
    "ContextQueueHandler",
    "HumanFormatter",
    "JsonFormatter",
    "audit",
    "configure_logging",
    "redact",
    "stop_logging",
)
