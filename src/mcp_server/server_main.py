"""FastMCP 服务端启动入口。

用法：

    # Streamable HTTP（默认，推荐）
    $env:PYTHONPATH="$PWD\\src"
    .\\.venv\\Scripts\\python.exe -m mcp_server.server_main

    # stdio（由 MCP Client 作为子进程拉起）
    $env:MCP_TRANSPORT="stdio"
    .\\.venv\\Scripts\\python.exe -m mcp_server.server_main
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_server.server_config import (
    MCP_HOST,
    MCP_PATH,
    MCP_PORT,
    MCP_SERVER_NAME,
    MCP_TRANSPORT,
)
from mcp_server.tools import register_all_tools


mcp = FastMCP(
    name=MCP_SERVER_NAME,
    instructions="Harness 项目通过 MCP 暴露的工具服务。",
    version="1.0.0",
)

register_all_tools(mcp)


def main() -> None:
    """按配置启动 stdio 或 Streamable HTTP 传输。"""
    if MCP_TRANSPORT == "stdio":
        mcp.run(transport="stdio")
        return

    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        path=MCP_PATH,
    )


if __name__ == "__main__":
    main()
