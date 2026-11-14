"""任务流与 Agent 运行时的桥接层。

命中任务流后不再由独立执行器逐步直调模型，而是把流程规范封装成一条
HumanMsg 交给主 Agent 图执行（见 ``task_flow/injection.py``）：

- 步骤进度由 ``task_flow_step_update`` 工具写回图状态并推送前端；
- HITL、工具、沙箱全部复用主 Agent 图的既有能力。

本模块只保留对话入口所需的路由集成：召回 + LLM 终选。

agent 与 api 依赖全部延迟导入：``src/task_flow`` 被独立导入时不拖入
Agent 运行时，保持模块可独立单测。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .router import FlowRouter, RouteDecision

logger = logging.getLogger(__name__)

_JUDGE_TEMPLATE = """你是任务流路由判别器。判断用户消息是否明确要求执行下列某个任务流。

用户消息：
{message}

候选任务流：
{candidates}

规则：
- 只有当用户的任务确实属于该流程覆盖的任务时才选择；仅仅出现相同名词（如“代码”“报告”“文档”）不算。
- 如果用户要求的是流程之外的另一种任务（例如流程是“代码审查”，用户要求“编译代码”），返回 none。
- 只能返回候选中的 flow_id；无法确定时返回 none，不要猜测。

只输出 JSON：{{"flow_id": "<id 或 none>", "reason": "<一句话理由>"}}
"""


def _candidate_text(search_doc: dict[str, Any]) -> str:
    examples = " / ".join(search_doc.get("trigger_examples", []) or [])
    steps = str(search_doc.get("step_summary", "") or "")
    return (
        f"- flow_id: {search_doc.get('flow_id')}\n"
        f"  名称：{search_doc.get('name', '')}\n"
        f"  描述：{search_doc.get('description', '')}\n"
        f"  触发示例：{examples}\n"
        f"  步骤：{steps}"
    )


async def judge_flow(
    message: str, candidates: list[dict[str, Any]]
) -> Optional[str]:
    """用摘要模型对候选流做一次终选；无法确定时返回 None。"""
    if not candidates:
        return None
    from agent.config import get_summary_model

    prompt = _JUDGE_TEMPLATE.format(
        message=message,
        candidates="\n".join(_candidate_text(item) for item in candidates),
    )
    response = await get_summary_model().ainvoke(
        [{"role": "user", "content": prompt}]
    )
    text = _content_to_text(getattr(response, "content", response))
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    selected = str(payload.get("flow_id") or "").strip()
    valid = {str(item.get("flow_id") or "") for item in candidates}
    return selected if selected in valid else None


async def _shadow_tier(message: str, user_id: str, decision: Optional[RouteDecision]) -> None:
    """影子评估旁路：任何失败都不影响主流程。"""
    try:
        from scheduler.shadow import shadow_evaluate

        await shadow_evaluate(message, user_id, decision)
    except Exception:
        logger.exception("调度影子评估异常，已忽略")


async def route_task_flow(
    message: str,
    user_id: str,
    *,
    flow_id: Optional[str] = None,
    entry_step: Optional[str] = None,
    user_state: str = "",
) -> Optional[RouteDecision]:
    """对话入口的任务流路由前置查询。

    命中（hit / takeover）返回 RouteDecision；未命中（miss / suggest）返回 None。
    任何路由异常都降级为 None——任务流不可用时不能影响主 Agent 流程。
    """
    try:
        from api.task_flow import get_store

        store = await get_store()
        decision = await FlowRouter(store, judge=judge_flow).route(
            message,
            user_id,
            flow_id=flow_id,
            entry_step=entry_step,
            user_state=user_state,
        )
    except Exception:
        logger.exception("任务流路由失败，降级为通用 Agent 流程")
        await _shadow_tier(message, user_id, None)
        return None
    await _shadow_tier(message, user_id, decision)
    if decision.hit:
        logger.info(
            "任务流命中：flow=%s kind=%s score=%s",
            decision.flow.id if decision.flow else "",
            decision.kind,
            decision.candidates[0]["score"] if decision.candidates else "-",
        )
    return decision if decision.hit else None


def _content_to_text(content: Any) -> str:
    """把模型返回的 content（字符串或内容块列表）转成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


__all__ = ["judge_flow", "route_task_flow"]
