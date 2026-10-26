"""业务无关的 FastMCP 冒烟服务。

用途：
- 验证 MCP Client 的工具发现与调用；
- 验证 stdio 与 Streamable HTTP 两种传输。

运行：
    python test/mcp_demo_server.py          # stdio
    python test/mcp_demo_server.py --http   # Streamable HTTP
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from fastmcp import FastMCP


mcp = FastMCP(
    name="harness-mcp-demo",
    instructions="用于验证 MCP 连接、工具发现与调用的最小服务。",
)


@mcp.tool(name="echo")
def echo(text: str) -> str:
    """原样返回输入文本。

    Args:
        text: 要回显的文本。
    """
    return text


@mcp.tool(name="add")
def add(a: int, b: int) -> int:
    """返回两个整数之和。

    Args:
        a: 第一个整数。
        b: 第二个整数。
    """
    return a + b


@mcp.tool(name="current_time")
def current_time() -> str:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    """按命令行参数启动 HTTP 或 stdio 传输。"""
    if "--http" in sys.argv:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=int(os.getenv("MCP_DEMO_PORT", "18091")),
            path="/mcp",
        )
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
