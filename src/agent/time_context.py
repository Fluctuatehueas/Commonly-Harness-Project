"""对话与任务计划的时间上下文。

设计约定（与提示词解耦）：

- 时间戳是消息自身的属性，写在 ``additional_kwargs`` 里随 checkpoint 持久化；
- 只在模型调用时，把最近若干条消息的时间压成一条「对话时间线」附在当前用户
  消息末尾，Agent 由此获得时间感知；
- 不再把时间戳逐条渲染进消息正文：模型会把它当成正文模板照抄进回答；
- 输出的正文统一经过 :func:`strip_time_tags` 清洗，历史里被污染的时间戳标记
  也不会再出现在气泡里；气泡时间由前端读取消息自身时间展示。

时区默认 ``Asia/Shanghai``，可用 ``HARNESS_TIMEZONE`` 覆盖。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# 消息 additional_kwargs 中的时间戳键。OpenAI 兼容端点只透传少数已知键，
# 该键不会被发送到模型服务，可以安全地作为消息属性持久化。
MESSAGE_TIME_KEY = "timestamp"

_DEFAULT_TIMEZONE = "Asia/Shanghai"
# 超过这个跨度就不再显示相对时间，避免「1234 天前」这类无信息量的前缀。
_RELATIVE_MAX_DAYS = 30

# 时间标记清洗：兼容历史版本写入的 <系统时间 …> 与 [2026-11-13 19:39:24 +08:00]。
_TIME_TAG_RE = re.compile(
    r"<{1,2}\s*系统时间[^<>]*>{1,2}"
    r"|\[\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"
    r"(?:\s*[+-]\d{2}:?\d{2})?(?:\s*·[^\]]*)?\s*\]"
)

def timezone_name() -> str:
    """返回生效的时区名，供计划和工具输出标注。"""
    return (os.getenv("HARNESS_TIMEZONE") or _DEFAULT_TIMEZONE).strip() or _DEFAULT_TIMEZONE


def local_timezone() -> timezone | ZoneInfo:
    """解析时区；Windows 缺少 tzdata 时退回系统本地时区。"""
    try:
        return ZoneInfo(timezone_name())
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def now() -> datetime:
    return datetime.now(local_timezone())


def now_iso() -> str:
    """当前时间：带时区偏移的 ISO 字符串（精确到秒）。"""
    return now().isoformat(timespec="seconds")


def to_iso(value: datetime) -> str:
    """把 datetime 归一化为带时区的 ISO 字符串。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def parse_iso(value: Any) -> datetime | None:
    """解析消息或计划里的时间戳；无法解析时返回 None（调用方按缺失处理）。"""
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def message_timestamp(message: Any) -> str:
    """读取消息上已有的时间戳，兼容 LangChain 消息对象与 checkpoint 字典。"""
    if isinstance(message, dict):
        additional = message.get("additional_kwargs") or {}
    else:
        additional = getattr(message, "additional_kwargs", None) or {}
    if not isinstance(additional, dict):
        return ""
    return str(additional.get(MESSAGE_TIME_KEY) or "")


def stamp_message(message: Any, value: str | None = None) -> Any:
    """返回带时间戳的消息副本；已有时间戳时不覆盖。"""
    if isinstance(message, dict) or message_timestamp(message):
        return message
    additional = dict(getattr(message, "additional_kwargs", None) or {})
    additional[MESSAGE_TIME_KEY] = value or now_iso()
    return message.model_copy(update={"additional_kwargs": additional})


def _offset(value: datetime) -> str:
    raw = value.strftime("%z")
    return f"{raw[:3]}:{raw[3:]}" if len(raw) == 5 else ""


def format_absolute(value: datetime) -> str:
    return f"{value.strftime('%Y-%m-%d %H:%M:%S')} {_offset(value)}".strip()


def format_relative(value: datetime, reference: datetime | None = None) -> str:
    """把时间差转成「刚刚 / N 分钟前 / N 小时前 / N 天前」。"""
    seconds = int(((reference or now()) - value).total_seconds())
    if seconds < 0:
        return ""
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    days = seconds // 86400
    return f"{days}天前" if days <= _RELATIVE_MAX_DAYS else ""


def strip_time_tags(text: str) -> str:
    """清掉正文里的时间戳标记（含历史遗留格式），返回可展示文本。"""
    if not text:
        return text
    cleaned = _TIME_TAG_RE.sub("", text)
    if cleaned == text:
        return text
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


__all__ = [
    "MESSAGE_TIME_KEY",
    "format_absolute",
    "format_relative",
    "local_timezone",
    "message_timestamp",
    "now",
    "now_iso",
    "parse_iso",
    "stamp_message",
    "strip_time_tags",
    "timezone_name",
    "to_iso",
]
