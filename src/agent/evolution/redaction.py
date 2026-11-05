"""轨迹落库前的轻量脱敏。

目标是覆盖最常见的凭据与个人信息，不追求完整 DLP。脱敏失败不应阻断
Agent 主流程，调用方只记录告警。
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("bearer", re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1[REDACTED:TOKEN]"),
    ("api_key", re.compile(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+"), r"\1[REDACTED]"),
    ("email", re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+)"), r"[REDACTED:EMAIL]@\2"),
    ("phone", re.compile(r"1[3-9]\d{9}"), "[REDACTED:PHONE]"),
    (
        "abs_path",
        re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|\/home\/[^\s\"']+\/|\/Users\/[^\s\"']+\/)"),
        "[REDACTED:PATH]",
    ),
]

_SECRET_KEYS = {
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "token",
    "authorization",
    "access_token",
    "refresh_token",
}


def redact_text(value: Any) -> tuple[str, list[str]]:
    """脱敏一段文本，返回 (处理后文本, 命中的规则名)。"""
    text = "" if value is None else str(value)
    rules: list[str] = []
    for name, pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            rules.append(name)
    return text, rules


def redact_mapping(value: Any) -> tuple[Any, list[str]]:
    """递归脱敏字典/列表中的敏感字段与文本。"""
    rules: list[str] = []
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[REDACTED:SECRET]"
                rules.append("secret_field")
                continue
            cleaned_item, child_rules = redact_mapping(item)
            cleaned[key] = cleaned_item
            rules.extend(child_rules)
        return cleaned, sorted(set(rules))
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for item in value:
            cleaned_item, child_rules = redact_mapping(item)
            items.append(cleaned_item)
            rules.extend(child_rules)
        return items, sorted(set(rules))
    return redact_text(value)
