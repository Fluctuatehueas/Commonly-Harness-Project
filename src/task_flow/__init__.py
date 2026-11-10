"""任务流模块：让 Agent 有稳定的工作习惯。

编译期（确定性，不调用 LLM）：
    parse → validate → compile → build_search_doc
运行期（LLM 介入）：
    route → execute

设计文档见本目录 README.md；开发文档见 docs/develope/任务流模块开发文档.md。
"""

from __future__ import annotations

from typing import Any, Optional

from . import compiler, indexer, parser, validators
from .models import (
    Execution,
    ExecutionPlan,
    FlowIssue,
    FlowValidationError,
    TaskFlow,
)
from .validators import ValidationContext


def compile_flow(
    ui_payload: dict[str, Any],
    ctx: Optional[ValidationContext] = None,
) -> tuple[TaskFlow, ExecutionPlan, dict]:
    """编译期管线入口：解析 → 校验 → 编译 → 索引。

    校验失败抛 FlowValidationError（携带逐条 FlowIssue）。
    """
    flow = parser.parse(ui_payload)
    validators.run_all_or_raise(flow, ctx)
    plan = compiler.compile(flow)
    search_doc = indexer.build_search_doc(flow)
    return flow, plan, search_doc


__all__ = [
    "Execution",
    "ExecutionPlan",
    "FlowIssue",
    "FlowValidationError",
    "TaskFlow",
    "ValidationContext",
    "compile_flow",
    "compiler",
    "indexer",
    "parser",
    "validators",
]
