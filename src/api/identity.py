"""HTTP 层用户身份解析中间件。

前端通过 ``X-User-Id`` 头传递当前演示用户。中间件做两件事：

1. 把身份写入请求级 ContextVar，Agent / MCP / RAG / 技能 / 模型配置据此隔离；
2. 用请求头覆盖 query 中的 ``user_id``，让所有查询型接口自动按用户过滤，
   避免逐个接口改签名时遗漏。

请求体中的 ``user_id``（聊天、上传等）由各接口调用 ``effective_user_id``
显式覆盖，保证请求头始终是权威身份来源。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from starlette.datastructures import Headers

from agent.identity import (
    reset_current_user_id,
    safe_user_id,
    set_current_user_id,
)


USER_HEADER = "x-user-id"


class UserIdentityMiddleware:
    """纯 ASGI 中间件：设置 ContextVar 并把请求头身份写进 query。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        raw_header = Headers(scope=scope).get(USER_HEADER)
        header_user = safe_user_id(raw_header) if raw_header else ""
        token = set_current_user_id(header_user or "")

        if header_user:
            # 复制 scope，避免修改上游共享对象。
            scope = dict(scope)
            query = parse_qs(
                scope.get("query_string", b"").decode("latin-1"),
                keep_blank_values=True,
            )
            query["user_id"] = [header_user]
            scope["query_string"] = urlencode(query, doseq=True).encode("latin-1")

        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_user_id(token)


def effective_user_id(explicit: str | None) -> str:
    """请求头身份优先；无请求头时回退到请求体/查询参数。"""
    from agent.identity import current_user_id

    header_user = current_user_id(default="")
    return safe_user_id(header_user or explicit)
