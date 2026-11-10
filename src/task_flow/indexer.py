"""④ 流程索引器：从 schema 抽取可检索描述。

产出 search_doc 供运行期路由器召回。与流编译器职责分离：
编译器产出可执行计划，索引器产出可检索描述。
embedding 由发布流程在持久化时计算（需要异步模型调用），本模块只产文本。
"""

from __future__ import annotations

from typing import Any

from .models import TaskFlow


def build_search_doc(flow: TaskFlow) -> dict[str, Any]:
    """抽取名称、描述、触发示例与步骤摘要，组成检索文档。"""
    step_summaries: list[str] = []

    def walk(steps) -> None:
        for step in steps:
            summary = step.name if not step.instruction else f"{step.name}：{step.instruction}"
            step_summaries.append(summary)
            for sub in step.parallel or []:
                walk(sub.steps)

    walk(flow.steps)
    return {
        "flow_id": flow.id,
        "name": flow.name,
        "description": flow.description,
        "trigger_examples": list(flow.trigger_examples),
        "step_summary": "；".join(step_summaries),
        "source": flow.source,
    }


def recall_text(search_doc: dict[str, Any]) -> str:
    """把检索文档拼成单段文本，供关键词召回或 embedding 输入。"""
    parts = [
        search_doc.get("name", ""),
        search_doc.get("description", ""),
        " ".join(search_doc.get("trigger_examples", [])),
        search_doc.get("step_summary", ""),
    ]
    return "\n".join(p for p in parts if p)
