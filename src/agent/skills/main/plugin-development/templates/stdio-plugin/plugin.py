"""stdio 插件模板：一行一条 JSON-RPC 消息。"""

from __future__ import annotations

import json
import sys
from typing import Any

PLUGIN_ID = "my-plugin"


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


def tool_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name != "echo":
        raise ValueError(f"未知工具: {name}")
    return {"content": f"echo: {arguments.get('text', '')}"}


def handle(method: str, params: dict[str, Any]) -> Any:
    if method == "plugin.initialize":
        return {"ok": True}
    if method == "plugin.describe":
        return describe()
    if method == "plugin.health":
        return {"ok": True}
    if method == "tool.call":
        return tool_call(params)
    if method == "plugin.shutdown":
        return {"ok": True}
    raise ValueError(f"未实现方法: {method}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = message.get("id")
        method = message.get("method") or ""
        params = message.get("params") or {}
        try:
            result = handle(method, params)
        except Exception as error:  # noqa: BLE001 - 模板保持简单
            if request_id is not None:
                sys.stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32010, "message": str(error)},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
            continue
        if request_id is not None:
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "result": result},
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()
        if method == "plugin.shutdown":
            break


if __name__ == "__main__":
    main()
