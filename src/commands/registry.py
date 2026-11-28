"""命令注册表。

只登记宿主内置命令；目录在代码中静态维护，启动即可用，不读数据库。
``executor=backend`` 的命令由 ``commands.service`` 执行，前端命令由前端分发。
"""

from __future__ import annotations

from commands.schema import CommandError, CommandSpec, ERROR_NOT_FOUND

COMMANDS: tuple[CommandSpec, ...] = (
    # ---------- 查询类（后端执行） ----------
    CommandSpec(
        name="help",
        usage="/help [command]",
        summary="显示命令列表或单条命令用法",
        category="query",
        executor="backend",
        examples=("/help", "/help mcp"),
    ),
    CommandSpec(
        name="status",
        usage="/status",
        summary="查看后端、LLM、沙箱与会话用量",
        category="query",
        executor="backend",
    ),
    CommandSpec(
        name="token",
        usage="/token",
        summary="查看当前会话 Token 与上下文占用",
        category="query",
        executor="backend",
    ),
    CommandSpec(
        name="rag",
        usage="/rag",
        summary="查看知识库 Provider 连接状态",
        category="query",
        executor="backend",
    ),
    # ---------- 会话与界面（前端执行） ----------
    CommandSpec(
        name="delete",
        usage="/delete [-f]",
        summary="删除当前会话；-f 跳过确认",
        category="session",
        executor="frontend",
        risk="high",
        requires_confirm=True,
        flags=("-f",),
        examples=("/delete", "/delete -f"),
    ),
    CommandSpec(
        name="withdraw",
        usage="/withdraw",
        summary="撤回最新一条用户消息",
        category="session",
        executor="frontend",
        risk="medium",
    ),
    CommandSpec(
        name="retry",
        usage="/retry",
        summary="重试最后一条最终回答",
        category="session",
        executor="frontend",
        risk="medium",
    ),
    CommandSpec(
        name="stop",
        usage="/stop",
        summary="中止当前流式生成",
        category="session",
        executor="frontend",
    ),
    CommandSpec(
        name="new",
        usage="/new",
        summary="新建会话",
        category="session",
        executor="frontend",
    ),
    CommandSpec(
        name="rename",
        usage="/rename <title>",
        summary="重命名当前会话",
        category="session",
        executor="frontend",
    ),
    CommandSpec(
        name="clear",
        usage="/clear",
        summary="清空当前页面消息视图（不删除后端历史）",
        category="session",
        executor="frontend",
        risk="medium",
        requires_confirm=True,
    ),
    CommandSpec(
        name="copy",
        usage="/copy",
        summary="复制最后一条助手回答",
        category="basic",
        executor="frontend",
    ),
    CommandSpec(
        name="export",
        usage="/export [md|json]",
        summary="导出当前会话为 Markdown 或 JSON",
        category="basic",
        executor="frontend",
    ),
    CommandSpec(
        name="history",
        usage="/history [n]",
        summary="查看最近命令历史",
        category="basic",
        executor="frontend",
    ),
    CommandSpec(
        name="pin",
        usage="/pin",
        summary="置顶当前会话",
        category="session",
        executor="frontend",
    ),
    CommandSpec(
        name="unpin",
        usage="/unpin",
        summary="取消置顶当前会话",
        category="session",
        executor="frontend",
    ),
    CommandSpec(
        name="archive",
        usage="/archive",
        summary="归档当前会话",
        category="session",
        executor="frontend",
        risk="medium",
    ),
    CommandSpec(
        name="think",
        usage="/think [on|off]",
        summary="切换思考模式",
        category="basic",
        executor="frontend",
    ),
    CommandSpec(
        name="web",
        usage="/web [on|off]",
        summary="切换联网搜索",
        category="basic",
        executor="frontend",
    ),
    # ---------- 资源绑定（前端解析 + 后端生效） ----------
    CommandSpec(
        name="agent",
        usage="/agent [name] [-team] [team]",
        summary="绑定下一轮使用的 Agent 与组",
        category="resource",
        executor="frontend",
        candidates="agents",
        flags=("-team",),
        examples=("/agent writer", "/agent writer -team 研发组"),
    ),
    CommandSpec(
        name="skills",
        usage="/skills [name|off|list]",
        summary="绑定下一轮优先使用的技能",
        category="resource",
        executor="frontend",
        candidates="skills",
        examples=("/skills", "/skills frontend-design", "/skills off"),
    ),
    CommandSpec(
        name="mcp",
        usage="/mcp [name|off|list|status]",
        summary="绑定下一轮使用的 MCP 服务",
        category="resource",
        executor="frontend",
        candidates="mcp",
        examples=("/mcp slack", "/mcp off"),
    ),
    CommandSpec(
        name="plugin",
        usage="/plugin [name|off|list|status|get <url> [IsUse]]",
        summary="绑定下一轮使用的插件，或安装新插件",
        category="resource",
        executor="frontend",
        candidates="plugins",
        examples=("/plugin text-toolkit", "/plugin get https://…/p.zip"),
    ),
    CommandSpec(
        name="taskflow",
        usage="/taskflow [name|off|list]",
        summary="绑定下一轮使用的任务流",
        category="resource",
        executor="frontend",
        candidates="taskflows",
        examples=("/taskflow 周报", "/taskflow off"),
    ),
    CommandSpec(
        name="binding",
        usage="/binding",
        summary="查看本轮资源绑定",
        category="resource",
        executor="frontend",
    ),
    CommandSpec(
        name="skill",
        usage="/skill get <url> [IsUse] | /skill show <name>",
        summary="下载技能或预览技能内容",
        category="install",
        executor="frontend",
        risk="medium",
        examples=("/skill get https://…/skill.zip", "/skill show frontend-design"),
    ),
    # ---------- 只读列表（Agent 工具与前端 list 子命令共用） ----------
    CommandSpec(
        name="list_agents",
        usage="/list_agents",
        summary="列出当前用户可见的 Agent（只读）",
        category="query",
        executor="backend",
        visible=False,
    ),
    CommandSpec(
        name="list_skills",
        usage="/list_skills",
        summary="列出当前用户可见的技能（只读）",
        category="query",
        executor="backend",
        visible=False,
    ),
    CommandSpec(
        name="list_mcp",
        usage="/list_mcp",
        summary="列出 MCP 服务与连接状态（只读）",
        category="query",
        executor="backend",
        visible=False,
    ),
    CommandSpec(
        name="list_plugins",
        usage="/list_plugins",
        summary="列出已安装插件（只读）",
        category="query",
        executor="backend",
        visible=False,
    ),
    CommandSpec(
        name="list_taskflows",
        usage="/list_taskflows",
        summary="列出任务流（只读）",
        category="query",
        executor="backend",
        visible=False,
    ),
)

_BY_NAME: dict[str, CommandSpec] = {item.name: item for item in COMMANDS}


def get_spec(name: str) -> CommandSpec:
    spec = _BY_NAME.get(str(name or "").strip().lower())
    if spec is None:
        raise CommandError(ERROR_NOT_FOUND, f"未知命令: /{name}", status=404)
    return spec


def find_spec(name: str) -> CommandSpec | None:
    return _BY_NAME.get(str(name or "").strip().lower())


def catalog(*, visible_only: bool = True) -> list[dict]:
    items = [item for item in COMMANDS if item.visible or not visible_only]
    return [item.to_dict() for item in items]


def backend_commands() -> set[str]:
    return {item.name for item in COMMANDS if item.executor == "backend"}


__all__ = [
    "COMMANDS",
    "backend_commands",
    "catalog",
    "find_spec",
    "get_spec",
]
