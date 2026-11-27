"""文本工具箱：统计、转换与字面替换。

纯函数、无外部依赖；输入长度受限，避免在宿主进程内产生大对象。
"""

from __future__ import annotations

import json
import re
from typing import Any

PLUGIN_ID = "text-toolkit"
MAX_TEXT_CHARS = 20000
MAX_OUTPUT_CHARS = 200000
_SLUG_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def describe() -> dict[str, Any]:
    return {
        "plugin_id": PLUGIN_ID,
        "protocolVersion": "1.0",
        "capabilities": {
            "tool": [
                {
                    "name": "text_stats",
                    "description": "统计字符数、词数、行数与段落数。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string", "description": "待统计文本"}},
                        "required": ["text"],
                    },
                },
                {
                    "name": "text_transform",
                    "description": "转换文本：upper / lower / title / trim / slug。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["upper", "lower", "title", "trim", "slug"],
                                "description": "转换方式",
                            },
                        },
                        "required": ["text", "mode"],
                    },
                },
                {
                    "name": "find_replace",
                    "description": "按字面量查找并替换文本，支持大小写与替换次数。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "search": {"type": "string", "description": "要查找的字面文本"},
                            "replace": {"type": "string", "description": "替换文本，默认空串"},
                            "count": {
                                "type": "integer",
                                "description": "最多替换次数；0 表示全部",
                            },
                            "case_sensitive": {"type": "boolean", "description": "是否区分大小写"},
                        },
                        "required": ["text", "search"],
                    },
                },
            ]
        },
    }


def _text(arguments: dict[str, Any]) -> str:
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text 超过 {MAX_TEXT_CHARS} 字符限制")
    return text


def _slugify(text: str) -> str:
    value = _SLUG_PATTERN.sub("-", text.strip()).strip("-")
    return value.lower()


def text_stats(arguments: dict[str, Any]) -> dict[str, Any]:
    text = _text(arguments)
    words = [item for item in re.split(r"\s+", text.strip()) if item]
    lines = text.splitlines() if text else []
    paragraphs = [item for item in re.split(r"\n\s*\n", text.strip()) if item.strip()]
    return {
        "chars": len(text),
        "chars_no_spaces": len(re.sub(r"\s", "", text)),
        "words": len(words),
        "lines": len(lines),
        "paragraphs": len(paragraphs),
    }


def text_transform(arguments: dict[str, Any]) -> str:
    text = _text(arguments)
    mode = str(arguments.get("mode") or "").strip().lower()
    if mode == "upper":
        result = text.upper()
    elif mode == "lower":
        result = text.lower()
    elif mode == "title":
        result = text.title()
    elif mode == "trim":
        result = text.strip()
    elif mode == "slug":
        result = _slugify(text)
    else:
        raise ValueError(f"不支持的 mode: {mode!r}")
    return result[:MAX_OUTPUT_CHARS]


def find_replace(arguments: dict[str, Any]) -> dict[str, Any]:
    text = _text(arguments)
    search = arguments.get("search")
    if not isinstance(search, str) or not search:
        raise ValueError("search 必须是非空字符串")
    replace = arguments.get("replace")
    replacement = replace if isinstance(replace, str) else ""
    count = arguments.get("count")
    limit = int(count) if isinstance(count, int) and count > 0 else 0
    case_sensitive = bool(arguments.get("case_sensitive", False))

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(search), flags)
    result, replaced = pattern.subn(replacement, text, count=limit)
    if len(result) > MAX_OUTPUT_CHARS:
        raise ValueError(f"替换结果超过 {MAX_OUTPUT_CHARS} 字符限制")
    return {"replaced": replaced, "text": result}


TOOLS = {
    "text_stats": text_stats,
    "text_transform": text_transform,
    "find_replace": find_replace,
}


def handle(method: str, params: dict[str, Any]) -> Any:
    if method == "plugin.initialize":
        return {"ok": True}
    if method == "plugin.describe":
        return describe()
    if method == "plugin.health":
        return {"ok": True}
    if method == "plugin.shutdown":
        return {"ok": True}
    if method == "tool.call":
        name = str(params.get("name") or "")
        tool = TOOLS.get(name)
        if tool is None:
            raise ValueError(f"未知工具: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是对象")
        result = tool(arguments)
        if isinstance(result, str):
            return {"content": result}
        return {"content": json.dumps(result, ensure_ascii=False)}
    raise ValueError(f"未实现方法: {method}")
