"""②b 结构校验 Pass：步骤合法 / 引用存在 / 并行合法 / 字段长度 / 无环占位。"""

from __future__ import annotations

from ..models import FlowIssue, Step, TaskFlow
from . import ValidationContext, register


def _walk_steps(flow: TaskFlow):
    """遍历全部步骤（含子流），产出 (step, depth)。"""
    stack: list[tuple[Step, int]] = [(s, 1) for s in flow.steps]
    while stack:
        step, depth = stack.pop()
        yield step, depth
        for sub in step.parallel or []:
            for child in sub.steps:
                stack.append((child, depth + 1))


def _steps_legal(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    issues: list[FlowIssue] = []
    if not flow.steps:
        issues.append(FlowIssue(pass_name="structure.steps", message="任务流至少需要一个步骤"))
    ids: set[str] = set()
    names: set[str] = set()
    for step, _ in _walk_steps(flow):
        if step.id in ids:
            issues.append(
                FlowIssue(pass_name="structure.steps", step_id=step.id, message=f"步骤 id 重复：{step.id}")
            )
        ids.add(step.id)
        if step.name in names:
            issues.append(
                FlowIssue(pass_name="structure.steps", step_id=step.id, message=f"步骤名称重复：{step.name}")
            )
        names.add(step.name)
    return issues


def _refs_exist(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    issues: list[FlowIssue] = []

    def check(resolver, value: str, kind: str, step_id: str | None) -> None:
        result = resolver(value)
        if result is False:
            issues.append(
                FlowIssue(
                    pass_name="structure.refs",
                    step_id=step_id,
                    message=f"引用的{kind}不存在：{value}",
                )
            )
        elif result is None:
            issues.append(
                FlowIssue(
                    pass_name="structure.refs",
                    step_id=step_id,
                    level="warning",
                    message=f"无法确认{kind}是否存在（注册表不可用）：{value}",
                )
            )

    for step, _ in _walk_steps(flow):
        if step.skills != "auto" and ctx.skill_exists:
            for skill in step.skills:
                check(ctx.skill_exists, skill, "SKILL", step.id)
        if step.executor.startswith("subagent:") and ctx.role_exists:
            check(ctx.role_exists, step.executor.split(":", 1)[1], "SubAgent 角色", step.id)
    if ctx.mcp_exists:
        for service in flow.mcp_services:
            check(ctx.mcp_exists, service, "MCP 服务", None)
    return issues


def _parallel_legal(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    issues: list[FlowIssue] = []
    for step, depth in _walk_steps(flow):
        branches = step.parallel or []
        if not branches:
            continue
        if depth > ctx.max_parallel_depth:
            issues.append(
                FlowIssue(
                    pass_name="structure.parallel",
                    step_id=step.id,
                    message=f"并行子流嵌套层级 {depth} 超过上限 {ctx.max_parallel_depth}",
                )
            )
        # 分支数量不设下限：允许先建 1 条分支，运行时会按已有分支执行。
        for branch in branches:
            if not branch.steps:
                issues.append(
                    FlowIssue(
                        pass_name="structure.parallel",
                        step_id=step.id,
                        message=f"并行分支「{branch.name}」至少需要 1 个步骤",
                    )
                )
    return issues


def _field_length(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    issues: list[FlowIssue] = []
    if len(flow.name) > ctx.max_name_chars:
        issues.append(
            FlowIssue(pass_name="structure.length", message=f"流名称超长（>{ctx.max_name_chars}）")
        )
    if len(flow.description) > ctx.max_description_chars:
        issues.append(
            FlowIssue(pass_name="structure.length", message=f"流描述超长（>{ctx.max_description_chars}）")
        )
    for step, _ in _walk_steps(flow):
        if len(step.name) > ctx.max_name_chars:
            issues.append(
                FlowIssue(pass_name="structure.length", step_id=step.id, message="步骤名称超长")
            )
        if len(step.instruction) > ctx.max_instruction_chars:
            issues.append(
                FlowIssue(pass_name="structure.length", step_id=step.id, message="步骤指令超长")
            )
        if step.example and len(step.example) > ctx.max_example_chars:
            issues.append(
                FlowIssue(pass_name="structure.length", step_id=step.id, message="步骤示例超长")
            )
    return issues


def _acyclic(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    """无环占位：v1 子流内联定义、不可引用主流，天然无环。

    若未来支持流间引用，在此实现显式环检测（环须显式声明）。
    """
    return []


register("structure.steps", _steps_legal)
register("structure.refs", _refs_exist)
register("structure.parallel", _parallel_legal)
register("structure.length", _field_length)
register("structure.acyclic", _acyclic)
