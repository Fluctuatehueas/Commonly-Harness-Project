"""RAG 检索工具：让主 Agent 使用已注册的企业知识库。

Harness 不保存知识原文；工具返回的是 Provider 给出的切片与引用信息。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool

from rag.runtime import rag_runtime
from rag.types import RagError


def create_rag_tools() -> list[Any]:
    """创建 RAG 相关工具；无 Provider 时调用会返回空结果而不是报错。"""

    @tool
    async def knowledge_search(
        query: Annotated[str, "要在企业知识库中检索的问题或关键词"],
        top_k: Annotated[int, "返回的知识片段数量，1~10"] = 5,
        domain: Annotated[str, "知识域过滤，如 contract、crm，可空"] = "",
        provider_ids: Annotated[
            str, "指定 RAG Provider，多个用逗号分隔；留空表示全部"
        ] = "",
    ) -> dict[str, Any]:
        """检索已注册的企业知识库（RAG），返回带引用来源的知识片段。

        适用：企业制度、合同、产品资料、业务文档等事实性问题。
        不适用：需要实时业务数据的查询，请优先使用业务后端的 MCP 工具。
        引用必须来自返回的 citation_id，不得自行编造来源。
        """
        try:
            result = await rag_runtime.retrieve(
                query,
                provider_ids=[
                    item.strip()
                    for item in provider_ids.split(",")
                    if item.strip()
                ] or None,
                top_k=max(1, min(int(top_k or 5), 10)),
                filters={"domain": domain.strip()} if domain.strip() else {},
            )
        except RagError as error:
            return {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            }
        return {"ok": True, **result}

    return [knowledge_search]
