"""stdio 测试夹具：一个最小 JSON-RPC 插件。

只用于验证总线协议与反向 host 调用，不是对外 SDK 示例。
"""

from __future__ import annotations

import json
import sys


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        message = json.loads(text)
        method = message.get("method")
        request_id = message.get("id")
        if method == "plugin.initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}})
        elif method == "plugin.describe":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "plugin_id": "fixture",
                        "protocolVersion": "1.0",
                        "capabilities": {
                            "tool": [
                                {
                                    "name": "echo",
                                    "description": "回显",
                                    "inputSchema": {"type": "object", "properties": {}},
                                }
                            ]
                        },
                    },
                }
            )
        elif method == "plugin.health":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}})
        elif method == "tool.call":
            # 验证插件 → 总线的反向调用：先请求 host.storage.set，再读取响应。
            send(
                {
                    "jsonrpc": "2.0",
                    "id": "host-1",
                    "method": "host.storage.set",
                    "params": {"key": "k", "value": "v"},
                }
            )
            response_line = sys.stdin.readline()
            response = json.loads(response_line) if response_line.strip() else {}
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": f"host-ok:{bool((response.get('result') or {}).get('ok'))}"
                    },
                }
            )
        elif method == "plugin.shutdown":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}})
            break
        elif request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )


if __name__ == "__main__":
    main()
