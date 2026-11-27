"""inprocess 内置插件模板：实现 handle(method, params)。

入口由 manifest 的 runtime.entry 指向，例如 ``plugin:handle``。
"""

from __future__ import annotations

from typing import Any

PLUGIN_ID = "my-builtin-plugin"


def describe() -> dict[str, Any]:
    return {
        "plugin_id": PLUGIN_ID,
        "protocolVersion": "1.0",
        "capabilities": {
            "tool": [
                {
                    "name": "echo",
                    "description": "回显输入文本",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要回显的文本"}
                        },
                        "required": ["text"],
                    },
                }
            ]
        },
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
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "echo":
            raise ValueError(f"未知工具: {name}")
        return {"content": f"echo: {arguments.get('text', '')}"}
    raise ValueError(f"未实现方法: {method}")
