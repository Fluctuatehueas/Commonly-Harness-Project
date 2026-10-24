"""使用调用方共享的沙箱引用启动 Agent Protocol 异步任务。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from langgraph_sdk import get_client

from agent.backends.sandbox_proxy import SandboxBackendProxy


START_ASYNC_TASK_TOOL_NAME = "start_async_task"


def create_async_sandbox_tools(
    async_subagents: list[dict[str, Any]],
    *,
    sandbox_backend: SandboxBackendProxy,
) -> list[Any]:
    """构建异步任务提交工具，并把当前沙箱 ID 转发给远程子 Agent。"""
    registrations = {str(item["name"]): item for item in async_subagents}

    @tool
    async def start_async_task(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        """启动后台子 Agent，并将任务状态写入主图供后续查询。

        Args:
            description: 交给子 Agent 的完整任务描述，应包含目标、约束和期望交付物。
            subagent_type: 已注册的异步子 Agent 名称。
        """
        registration = registrations.get(subagent_type)
        if registration is None:
            available = ", ".join(sorted(registrations)) or "（无）"
            return f"未知的异步子 Agent: {subagent_type}；可用值: {available}"

        try:
            sandbox_id = await asyncio.to_thread(lambda: sandbox_backend.id)
            client = get_client(url=str(registration["url"]))
            thread = await client.threads.create()
            run = await client.runs.create(
                thread_id=thread["thread_id"],
                assistant_id=str(registration["graph_id"]),
                input={"messages": [{"role": "user", "content": description}]},
                context={
                    "sandbox_id": sandbox_id,
                    "subagent_name": subagent_type,
                },
            )
        except Exception as error:
            return f"启动异步子 Agent 失败: {error}"

        # 自定义启动流程必须维护 DeepAgents 的 async_tasks 状态；
        # 否则默认的 list_async_tasks 只会读取空状态，无法查询刚提交的任务。
        task_id = str(thread["thread_id"])
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        tracked_task = {
            "task_id": task_id,
            "agent_name": subagent_type,
            "thread_id": task_id,
            "run_id": str(run["run_id"]),
            "status": "running",
            "created_at": now,
            "last_checked_at": now,
            "last_updated_at": now,
        }
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"异步任务已启动。task_id: {task_id}",
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
                "async_tasks": {task_id: tracked_task},
            }
        )

    return [start_async_task]
