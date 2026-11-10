"""将运行时用户身份与项目工作区注入主 Agent 的系统提示词。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from agent.project_context import current_project_optional


class ContextInjectionMiddleware(AgentMiddleware):
    """把运行时的用户身份和当前项目写入每次主模型调用的系统提示词。"""

    name = "context_injection"

    @staticmethod
    def _context_notice(context: Any) -> str | None:
        """构造当前调用可见的用户身份说明，缺失身份时跳过注入。"""
        user_id = str(getattr(context, "user_id", "") or "").strip()
        if not user_id:
            return None
        username = str(getattr(context, "username", "") or user_id).strip()
        return (
            "当前运行时用户信息：\n"
            f"- user_id: {user_id}\n"
            f"- username: {username}\n"
            f"- 偏好文件: /memories/{user_id}/preferences.md\n"
            "处理本轮任务前先读取该偏好文件；recent_queries 由系统自动维护。"
        )

    @staticmethod
    def _project_notice() -> str | None:
        """构造当前会话的项目工作区说明；未绑定项目时返回 None。"""
        project = current_project_optional()
        if project is None:
            return None
        if project.kind == "free":
            return (
                "当前会话模式：自由会话（未绑定本机项目目录）。\n"
                "- 文件工具的 `/` 是本会话在沙箱内的独立目录，文件只保留在沙箱。\n"
                "- 不要声称能看到本机某个项目目录；用户需要操作本机项目时，"
                "提示其在侧边栏「打开已有项目」并在该项目下新建对话。"
            )
        root = str(project.root) if project.root is not None else "（目录不可用）"
        lines = [
            "当前会话模式：项目会话（已绑定本机项目目录）。",
            f"- 项目名称：{project.name}",
            f"- 项目根目录：{root}",
            f"- 项目状态：{project.status}（目录标记：{project.marker_state}）",
            "- 文件工具中的 `/` 就是该项目根目录，可以直接列目录、读取现有文件；"
            "写入也会落到该项目。",
        ]
        if project.status != "active":
            lines.append(
                "- 该项目当前不可写，写操作会被拒绝；如需修改，请让用户在项目菜单中修复目录标记。"
            )
        return "\n".join(lines)

    def _inject_context(self, request: ModelRequest) -> ModelRequest:
        """返回包含运行时上下文的模型请求，不修改持久化消息历史。"""
        runtime = request.runtime
        notices = [
            notice
            for notice in (
                self._context_notice(getattr(runtime, "context", None)),
                self._project_notice(),
            )
            if notice
        ]
        if not notices:
            return request
        notice = "\n\n".join(notices)
        system_message = request.system_message or SystemMessage(content="")
        content = system_message.content
        if isinstance(content, str):
            injected_content = f"{content}\n\n{notice}".strip()
        else:
            injected_content = [*content, {"type": "text", "text": notice}]
        return request.override(
            system_message=SystemMessage(content=injected_content),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """在同步模型调用前注入用户上下文。"""
        return handler(self._inject_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """在异步模型调用前注入用户上下文。"""
        return await handler(self._inject_context(request))
