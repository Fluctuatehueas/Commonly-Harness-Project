"""命令执行服务：校验命令归属、分发处理器、格式化 Agent 文本结果。"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from commands import registry
from commands.handlers import HANDLERS
from commands.schema import (
    CommandContext,
    CommandError,
    CommandResult,
    ERROR_EXECUTOR_MISMATCH,
    ERROR_NOT_ALLOWED,
    ERROR_NOT_FOUND,
)

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 5.0


async def execute(ctx: CommandContext) -> CommandResult:
    """执行一条后端命令；前端命令在这里直接拒绝。"""
    spec = registry.find_spec(ctx.name) or registry.find_spec(
        str(ctx.raw or "").split(" ", 1)[0].lstrip("/")
    )
    if spec is None:
        raise CommandError(ERROR_NOT_FOUND, f"未知命令: /{ctx.name or ctx.raw}", status=404)
    if spec.executor != "backend":
        raise CommandError(
            ERROR_EXECUTOR_MISMATCH,
            f"/{spec.name} 由前端执行，不能通过后端接口调用",
            status=400,
        )
    handler = HANDLERS.get(spec.name)
    if handler is None:
        raise CommandError(ERROR_NOT_FOUND, f"/{spec.name} 还没有可用处理器", status=404)
    try:
        return await asyncio.wait_for(handler(ctx), timeout=COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as error:
        raise CommandError("COMMAND_TIMEOUT", f"/{spec.name} 执行超时") from error


def _format_result(result: CommandResult) -> str:
    lines: list[str] = []
    title = result.title or f"/{result.command}"
    lines.append(title)
    if result.text:
        lines.append(result.text)
    if result.kind == "table" and result.columns:
        lines.append(" | ".join(str(item) for item in result.columns))
        lines.append("-" * 40)
        for row in result.rows:
            lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
    for warning in result.warnings:
        lines.append(f"警告：{warning}")
    if result.ok is False:
        lines.append("结果：失败")
    return "\n".join(lines).strip()


def _parse_command_line(line: str) -> tuple[str, list[str], dict[str, Any]]:
    text = str(line or "").strip()
    if text.startswith("/"):
        text = text[1:]
    try:
        parts = shlex.split(text)
    except ValueError as error:
        raise CommandError("COMMAND_BAD_SYNTAX", f"命令语法错误：{error}") from error
    if not parts:
        raise CommandError("COMMAND_BAD_SYNTAX", "命令不能为空")
    name = parts[0].strip().lower()
    args: list[str] = []
    flags: dict[str, Any] = {}
    for item in parts[1:]:
        if item.startswith("-") and item != "-":
            key = item.lstrip("-")
            if key:
                flags[key] = True
            continue
        args.append(item)
    return name, args, flags


async def execute_for_agent(
    user_id: str,
    thread_id: str,
    command_line: str,
) -> str:
    """Agent 工具入口：只允许只读后端命令，返回可直接阅读的文本。"""
    name, args, flags = _parse_command_line(command_line)
    spec = registry.find_spec(name)
    if spec is None or spec.executor != "backend":
        raise CommandError(
            ERROR_NOT_ALLOWED,
            f"Agent 只能执行只读查询命令，/{name} 不在白名单内",
        )
    ctx = CommandContext(
        user_id=user_id,
        name=name,
        thread_id=thread_id,
        args=args,
        flags=flags,
        raw=f"/{name}",
    )
    result = await execute(ctx)
    return _format_result(result)


__all__ = ["COMMAND_TIMEOUT_SECONDS", "execute", "execute_for_agent"]
