"""FastMCP 工具注册入口。

接入方在这里导入并注册自己的工具组：

    from mcp_server.tools.my_tools import register_my_tools

    def register_all_tools(mcp):
        register_my_tools(mcp)

工具函数只需使用 FastMCP 的 ``@mcp.tool`` 装饰器，协议适配由 FastMCP 负责。
"""
from __future__ import annotations

import asyncio

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

def register_all_tools(mcp: FastMCP) -> None:
    """注册当前项目要暴露的全部 MCP 工具。"""
    # 默认不注册任何业务工具；接入方在此追加自己的工具组。
    @mcp.tool
    def count_text_stats(
        text: Annotated[str, Field(description="要统计的文本内容")],
    ) -> dict[str, int]:
        """统计文本的字符数、行数和非空行数。

        适合在 Agent 处理日志、代码或文档时，快速拿到规模信息。
        """
        lines = text.splitlines()
        return {
            "chars": len(text),
            "lines": len(lines),
            "non_empty_lines": sum(1 for line in lines if line.strip()),
        }

    @mcp.tool
    def add_numbers(
        a: Annotated[float, Field(description="第一个加数")],
        b: Annotated[float, Field(description="第二个加数")],
    ) -> float:
        """返回两个数字之和。可用于验证工具注册链路是否通畅。"""
        return a + b

    @mcp.tool
    async def delayed_echo(
        message: Annotated[str, Field(description="要回显的消息")],
        delay_seconds: Annotated[
            float, Field(ge=0, le=10, description="延迟秒数，范围 0–10")
        ] = 0.5,
    ) -> str:
        """等待指定秒数后回显消息。演示 async 工具与带默认值的参数。"""
        await asyncio.sleep(delay_seconds)
        return f"echo: {message}"
    return None
