"""
一个Demo中间件，使用DeepAgent框架。
该中间件负责统计罗列Agent的tool
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langchain.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

class ToolSummaryAgentMiddleWare(AgentMiddleware):
    """统计并罗列 Agent 可用工具，同时记录每个工具被调用的次数与耗时"""

    def __init__(self,tools: list | None = None) -> None:
        super().__init__()
        # 显示传入的工具列表
        self._declared_tools = tools
        # 运行时计数器
        self.tool_call_counts: dict[str,int] = {}

    def before_agent(self, state, runtime) -> None:
        """
        Agent 启动时，与用户对话之前
        罗列可用工具
        """
        tools = self._declared_tools or getattr(self, "tools",None) or []
        tool_names = [getattr(t,"name",str(t)) for t in tools] if tools else []
        print(f"[ToolSummaryAgentMiddleWare] Agent 启动，可用工具{len(tool_names)} 个: {tool_names}")
        return None

    def wrap_tool_call(
            self, 
            request : ToolCallRequest,
            handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        """
        缠绕钩子：每次工具调用前执行
        返回一个Command来更新状态
        """
        tool_name = request.tool_call["name"]
        args = request.tool_call.get("args",{})

        # 计数
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name,0) + 1
        start = time.time() # 开始时间戳
        try:
            result = handler(request) # 执行请求
            elapsed = time.time() - start # 耗时
            print(
                f"[工具调用] {tool_name}({args}) 成功，"
                f"累计 {self.tool_call_counts[tool_name]} 次，耗时 {elapsed:.3f}s"
            )
            return result
        except Exception as e:
            print(
                f"[工具调用] {tool_name} 失败: {e}，"
                f"累计 {self.tool_call_counts[tool_name]} 次，耗时 {elapsed:.3f}s"
            )
            raise  # 审计只记录，不吞异常[reference:2]

    def after_agent(
            self, 
            state : Any,
            runtime : Runtime) -> None:
        print("[工具统计] Agent 会话结束，工具调用汇总：")
        if not self.tool_call_counts:
            print("  （本次会话未发生任何工具调用）")
        else:
            for name, count in sorted(self.tool_call_counts.items()):
                print(f"  -{name}: {count}次")
        return None
        