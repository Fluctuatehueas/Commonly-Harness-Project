"""Agent 侧只读命令工具。

与 ``POST /commands/execute`` 共用 ``commands.service``；只允许
``executor=backend`` 的查询命令，删除、安装等写操作直接拒绝。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool

from commands.schema import CommandError
from commands.service import execute_for_agent


def create_command_tools() -> list[Any]:
    """创建 ``harness_command`` 工具；只读、低风险。"""

    @tool
    async def harness_command(
        command: Annotated[
            str,
            "要执行的只读命令，如 /status、/rag、/token、/list_agents、"
            "/list_skills、/list_mcp、/list_plugins、/list_taskflows",
        ],
    ) -> str:
        """查询 Harness 系统信息（只读）。

        适用：用户问“现在有哪些 Agent / MCP / 插件 / 技能 / 任务流”、
        “系统是否就绪”、“知识库连上了吗”、“本会话用了多少 Token”。
        不适用：删除会话、安装技能或插件、修改配置等写操作，这些命令会被拒绝。
        """
        from agent.identity import current_user_id

        try:
            return await execute_for_agent(current_user_id(), "", command)
        except CommandError as error:
            return f"Error: {error.message}"
        except Exception as error:  # noqa: BLE001 - 工具层必须返回文本
            return f"Error: 命令执行失败: {error}"

    return [harness_command]


__all__ = ["create_command_tools"]
