"""构造 Agent 共用的上下文压缩和调用次数保护中间件。"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models import BaseChatModel

from agent.context.budget import build_budget
from agent.middlewares.context_compaction import (
    HarnessCompactionToolMiddleware,
    HarnessContextCompactionMiddleware,
)


COMPACTION_SYSTEM_PROMPT = """
你可以在合适的时机主动调用 compact_conversation 压缩历史。判断标准：
- 应当调用：当前子任务已经收敛、结论已确认；准备进入完全不同的新阶段；已消化子 Agent 的长报告。
- 不应调用：正在推导中、关键工具调用尚未返回、等待人工审批或补充信息、答案尚未落定。
- 压缩摘要必须保留：已确认结论、审批/补充信息状态、异步 task_id、关键资源 ID、文件路径与未完成事项。
""".strip()


def build_agent_protection_middleware(
    *,
    backend: Any,
    summary_model: BaseChatModel,
    model_run_limit: int,
    tool_run_limit: int,
    enable_compaction_tool: bool = False,
) -> list[Any]:
    """创建一套摘要和循环保护中间件。

    自动摘要中间件与可选的 ``compact_conversation`` 工具共享同一摘要实例，
    因此手动和自动压缩会复用同一份状态和历史归档位置。调用次数只限制当前
    Agent run，避免长期会话因历史累计而失效。
    """
    # 压缩阈值按项目有效窗口计算，不再依赖模型 profile 的 1M 窗口。
    summarization = HarnessContextCompactionMiddleware(
        summary_model,
        backend,
        budget=build_budget(),
    )
    middleware: list[Any] = [summarization]
    if enable_compaction_tool:
        middleware.append(
            HarnessCompactionToolMiddleware(
                summarization,
                system_prompt=COMPACTION_SYSTEM_PROMPT,
            )
        )
    middleware.extend(
        [
            ModelCallLimitMiddleware(
                run_limit=model_run_limit,
                exit_behavior="end",
            ),
            ToolCallLimitMiddleware(
                run_limit=tool_run_limit,
                exit_behavior="end",
            ),
        ]
    )
    return middleware
