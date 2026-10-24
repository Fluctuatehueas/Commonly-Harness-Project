"""FastMCP 服务端的监听配置。"""

import os


MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "harness-mcp")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "18081"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
# streamable-http 或 stdio
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http").lower()
