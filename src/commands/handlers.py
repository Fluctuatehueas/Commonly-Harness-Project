"""只读命令处理器。

前端与 Agent 工具共用：所有处理器显式接收 ``user_id``，不读全局身份；
数据来源优先复用已有服务层，避免命令系统出现第二套口径。
"""

from __future__ import annotations

import logging
from typing import Any

from commands import registry
from commands.schema import CommandContext, CommandError, CommandResult, ERROR_NOT_FOUND

logger = logging.getLogger(__name__)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


async def handle_help(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        spec = registry.find_spec(ctx.args[0])
        if spec is None:
            raise CommandError(ERROR_NOT_FOUND, f"未知命令: /{ctx.args[0]}", status=404)
        text = "\n".join(
            [
                f"/{spec.name}",
                f"用法：{spec.usage}",
                f"说明：{spec.summary}",
                f"分类：{spec.category} · 执行方：{spec.executor} · 风险：{spec.risk}",
                *([f"示例：{' / '.join(spec.examples)}"] if spec.examples else []),
            ]
        )
        return CommandResult(
            command="help", kind="text", title=f"/{spec.name} 用法", text=text
        )
    rows = [
        [f"/{item['name']}", item["usage"], item["summary"]]
        for item in registry.catalog(visible_only=True)
    ]
    return CommandResult(
        command="help",
        kind="table",
        title="可用命令",
        columns=["命令", "用法", "说明"],
        rows=rows,
    )


async def _session_usage(user_id: str, thread_id: str) -> dict[str, Any]:
    if not thread_id:
        return {}
    try:
        from api.agent_loader import agent_loader

        data = await agent_loader.get_thread_usage(user_id, thread_id)
        return dict(data.get("session") or {})
    except Exception:
        logger.debug("读取会话用量失败", exc_info=True)
        return {}


async def handle_status(ctx: CommandContext) -> CommandResult:
    from api.settings import get_readiness

    readiness = await get_readiness()
    usage = await _session_usage(ctx.user_id, ctx.thread_id)
    rows: list[list[Any]] = [
        ["后端", "已连通（命令已到达服务端）"],
        [
            "LLM",
            f"{'已配置' if readiness['llm']['configured'] else '未配置'}"
            f"（{readiness['llm']['model_name'] or '-'}）",
        ],
        [
            "沙箱",
            f"{readiness['sandbox']['mode']}"
            f"（{'已配置' if readiness['sandbox']['configured'] else '未配置'}）",
        ],
        ["会话", ctx.thread_id or "-"],
    ]
    if usage:
        rows.extend(
            [
                ["输出 Token", usage.get("output", 0)],
                ["当前上下文", usage.get("context_used", 0)],
                ["上下文上限", usage.get("context_window", 0)],
                ["累计输入（计费）", usage.get("spent", 0)],
                ["已执行轮次", usage.get("rounds", 0)],
            ]
        )
    result = CommandResult(
        command="status",
        kind="table",
        title="系统状态",
        columns=["项目", "值"],
        rows=rows,
        warnings=list(readiness.get("issues") or []),
    )
    return result


async def handle_token(ctx: CommandContext) -> CommandResult:
    usage = await _session_usage(ctx.user_id, ctx.thread_id)
    if not usage:
        return CommandResult(
            command="token",
            kind="text",
            title="Token 用量",
            text="当前还没有会话用量；先发送一条消息后再查看。",
        )
    rows = [
        ["输出 Token", usage.get("output", 0)],
        ["当前上下文", usage.get("context_used", 0)],
        ["上下文上限", usage.get("context_window", 0)],
        ["累计输入（计费）", usage.get("spent", 0)],
        ["已执行轮次", usage.get("rounds", 0)],
    ]
    return CommandResult(
        command="token",
        kind="table",
        title="Token 用量",
        columns=["项目", "值"],
        rows=rows,
    )


async def handle_rag(ctx: CommandContext) -> CommandResult:
    from rag.runtime import rag_runtime

    items = list(rag_runtime.details())
    rows = [
        [
            item.get("name") or item.get("provider_id") or "",
            item.get("protocol", ""),
            item.get("state", ""),
            _cell(item.get("enabled")),
            item.get("last_checked_at") or "-",
            item.get("latency_ms") if item.get("latency_ms") is not None else "-",
            item.get("document_count") if item.get("document_count") is not None else "-",
        ]
        for item in items
    ]
    return CommandResult(
        command="rag",
        kind="table",
        title="知识库连接",
        columns=["名称", "协议", "状态", "启用", "最近连接", "延迟(ms)", "文档数"],
        rows=rows,
        meta={"total": len(rows)},
    )


async def handle_list_agents(ctx: CommandContext) -> CommandResult:
    from api.agents import list_agents

    payload = await list_agents(user_id=ctx.user_id)
    rows = [
        [
            item.get("name") or item.get("id"),
            item.get("id"),
            item.get("role_title") or "",
            _cell(item.get("is_default")),
            _cell(item.get("enabled")),
        ]
        for item in payload.get("items", [])
    ]
    return CommandResult(
        command="list_agents",
        kind="table",
        title="Agent 列表",
        columns=["名称", "ID", "岗位", "默认", "启用"],
        rows=rows,
    )


async def handle_list_skills(ctx: CommandContext) -> CommandResult:
    from api.skills import _scan_all

    items = _scan_all(ctx.user_id)
    rows = [
        [
            item.get("name", ""),
            item.get("title", ""),
            item.get("target", ""),
            item.get("source", ""),
            _cell(item.get("enabled")),
        ]
        for item in items
    ]
    return CommandResult(
        command="list_skills",
        kind="table",
        title="技能列表",
        columns=["名称", "标题", "归属", "来源", "启用"],
        rows=rows,
    )


async def handle_list_mcp(ctx: CommandContext) -> CommandResult:
    from agent.mcp.runtime import get_mcp_runtime

    items = get_mcp_runtime(ctx.user_id).details()
    rows = [
        [
            item.get("server_id") or item.get("id") or "",
            item.get("transport", ""),
            item.get("state", ""),
            _cell(item.get("enabled")),
            item.get("tool_count", 0),
            (item.get("description") or "")[:60],
        ]
        for item in items
    ]
    return CommandResult(
        command="list_mcp",
        kind="table",
        title="MCP 服务",
        columns=["服务", "传输", "状态", "启用", "工具数", "说明"],
        rows=rows,
    )


async def handle_list_plugins(ctx: CommandContext) -> CommandResult:
    from plugin_bus.runtime import get_plugin_runtime

    items = await get_plugin_runtime(ctx.user_id).list_installed()
    rows = [
        [
            item.get("plugin_id", ""),
            item.get("name", ""),
            item.get("version", ""),
            item.get("source", ""),
            _cell(item.get("enabled")),
            item.get("tool_count", 0),
        ]
        for item in items
    ]
    return CommandResult(
        command="list_plugins",
        kind="table",
        title="插件列表",
        columns=["ID", "名称", "版本", "来源", "启用", "工具数"],
        rows=rows,
    )


async def handle_list_taskflows(ctx: CommandContext) -> CommandResult:
    from api.task_flow import get_store

    store = await get_store()
    items, _total = await store.list_flows(ctx.user_id, 1, 100)
    rows = []
    for item in items:
        data = item if isinstance(item, dict) else getattr(item, "__dict__", {})
        rows.append(
            [
                data.get("name") or data.get("id") or "",
                data.get("id") or "",
                data.get("status") or "",
                data.get("enabled", data.get("status") != "disabled"),
            ]
        )
    return CommandResult(
        command="list_taskflows",
        kind="table",
        title="任务流列表",
        columns=["名称", "ID", "状态", "启用"],
        rows=rows,
    )


HANDLERS = {
    "help": handle_help,
    "status": handle_status,
    "token": handle_token,
    "rag": handle_rag,
    "list_agents": handle_list_agents,
    "list_skills": handle_list_skills,
    "list_mcp": handle_list_mcp,
    "list_plugins": handle_list_plugins,
    "list_taskflows": handle_list_taskflows,
}


__all__ = ["HANDLERS"]
