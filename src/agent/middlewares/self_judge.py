"""输出前两层自我裁定中间件。

L1 使用独立摘要模型做结构化自检；L2 使用确定性验证器。只对最终回答
生效，避免每次模型调用都增加固定开销。验证失败时有限重试一次，仍失败
则阻止原答案直接作为最终结果，并标记验证失败供后续归因。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from agent.config import (
    EVOLUTION_ENABLED,
    EVOLUTION_JUDGE_ENABLED,
    EVOLUTION_JUDGE_RISK_TIER,
    EVOLUTION_MODE,
)
from agent.evolution.judge import SelfJudge
from agent.evolution.types import RiskTier
from agent.evolution.verifiers import build_default_verifiers

logger = logging.getLogger(__name__)

HIGH_RISK_TOOLS = {
    "write_file",
    "edit_file",
    "host_write_file",
    "host_execute",
    "publish_to_project",
    "run_in_sandbox",
}

_CORRECTION_BLOCK = (
    "\n\n## 输出前自检未通过\n"
    "原因：{reason}\n"
    "请在不改变用户原始要求的前提下修正答案；无法确认的事实必须明确说明不确定。"
)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _last_ai_message(response: ModelResponse) -> Any | None:
    for message in reversed(list(getattr(response, "result", []) or [])):
        if getattr(message, "type", None) == "ai":
            return message
    return None


def _current_run_messages(state: dict[str, Any]) -> list[Any]:
    """只取最后一条用户消息之后的工具调用，避免历史 run 污染风险分级。"""
    messages = list((state or {}).get("messages", []) or [])
    start = 0
    for index, message in enumerate(messages):
        if getattr(message, "type", None) in {"human", "user"}:
            start = index
    return messages[start:]


def _tool_names(state: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for message in _current_run_messages(state):
        for call in list(getattr(message, "tool_calls", []) or []):
            if isinstance(call, dict) and call.get("name"):
                names.append(str(call["name"]))
    return names


def _user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) in {"human", "user"}:
            text = _message_text(message)
            if text:
                return text
    return ""


def _risk_tier(state: dict[str, Any], answer: str) -> RiskTier:
    names = _tool_names(state)
    if any(name in HIGH_RISK_TOOLS for name in names):
        return RiskTier.T2
    if names:
        return RiskTier.T1
    if any(char.isdigit() for char in answer) or "```" in answer:
        return RiskTier.T1
    return RiskTier.T0


def _should_judge(response: ModelResponse) -> tuple[Any, str] | None:
    message = _last_ai_message(response)
    if message is None:
        return None
    if list(getattr(message, "tool_calls", []) or []):
        return None
    text = _message_text(message)
    if not text:
        return None
    return message, text


class SelfJudgeMiddleware(AgentMiddleware):
    """在最终回答返回给用户前执行自检。"""

    def __init__(self, model: BaseChatModel) -> None:
        super().__init__()
        self._judge = SelfJudge(model)
        self._threshold = self._parse_threshold(EVOLUTION_JUDGE_RISK_TIER)

    @staticmethod
    def _parse_threshold(value: str) -> RiskTier:
        try:
            return RiskTier(value)
        except ValueError:
            return RiskTier.T1

    def _enabled_for(self, tier: RiskTier) -> bool:
        if not EVOLUTION_ENABLED or not EVOLUTION_JUDGE_ENABLED:
            return False
        if EVOLUTION_MODE == "observe":
            return False
        order = {RiskTier.T0: 0, RiskTier.T1: 1, RiskTier.T2: 2}
        return order[tier] >= order[self._threshold]

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        if not EVOLUTION_ENABLED or not EVOLUTION_JUDGE_ENABLED:
            return response
        candidate = _should_judge(response)
        if candidate is None:
            return response

        message, answer = candidate
        context = getattr(request.runtime, "context", None)
        state = getattr(request, "state", None) or {}
        tier = _risk_tier(state, answer)
        if not self._enabled_for(tier):
            return response

        user_text = _user_text(list(getattr(request, "messages", []) or []))
        verdict = await self._judge.judge_l1(user_text=user_text, answer=answer)
        verifiers = build_default_verifiers(context)
        l2_results = []
        if verdict.needs_verification and verifiers:
            l2_results = await self._judge.judge_l2(
                answer=answer,
                verifiers=verifiers,
                context=context,
            )

        l2_failed = [result for result in l2_results if not result.passed]
        if verdict.passed and not l2_failed:
            return response

        reason = verdict.reason or (
            "; ".join(result.detail for result in l2_failed) if l2_failed else "自检未通过"
        )
        corrected = await self._retry_with_correction(
            request=request,
            handler=handler,
            reason=reason,
        )
        if corrected is not None:
            retry_candidate = _should_judge(corrected)
            if retry_candidate is not None:
                retry_message, retry_answer = retry_candidate
                retry_verdict = await self._judge.judge_l1(
                    user_text=user_text,
                    answer=retry_answer,
                )
                retry_l2_failed = []
                if retry_verdict.needs_verification:
                    retry_l2_failed = [
                        result
                        for result in await self._judge.judge_l2(
                            answer=retry_answer,
                            verifiers=verifiers,
                            context=context,
                        )
                        if not result.passed
                    ]
                if retry_verdict.passed and not retry_l2_failed:
                    return corrected
                message, answer = retry_message, retry_answer
                response = corrected

        return self._block_or_annotate(
            response=response,
            message=message,
            reason=reason,
            tier=tier,
            context=context,
        )

    @staticmethod
    async def _retry_with_correction(
        *,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        reason: str,
    ) -> ModelResponse | None:
        try:
            system_message = request.system_message or SystemMessage(content="")
            content = system_message.content
            block = _CORRECTION_BLOCK.format(reason=reason)
            if isinstance(content, str):
                new_content = f"{content}\n{block}".strip()
            else:
                new_content = [*content, {"type": "text", "text": block}]
            return await handler(
                request.override(
                    system_message=SystemMessage(content=new_content),
                )
            )
        except Exception:
            logger.warning("自检修正重试失败", exc_info=True)
            return None

    @staticmethod
    def _block_or_annotate(
        *,
        response: ModelResponse,
        message: Any,
        reason: str,
        tier: RiskTier,
        context: Any,
    ) -> ModelResponse:
        try:
            setattr(context, "evolution_verification_failed", True)
            setattr(context, "evolution_verification_reason", reason)
            setattr(context, "evolution_risk_tier", tier.value)
        except Exception:
            pass

        if tier == RiskTier.T2:
            # 高风险任务失败关闭：不把可能错误的原答案直接作为最终交付。
            replacement = (
                "【验证未通过，已阻止自动继续】\n"
                f"原因：{reason}\n"
                "请人工确认后重试；系统已记录本次验证失败用于后续归因。"
            )
        else:
            replacement = (
                "【验证提示】以下答案未通过完整自检，请谨慎核对。\n"
                f"原因：{reason}\n\n{_message_text(message)}"
            )
        try:
            message.content = replacement
        except Exception:
            try:
                response.result = [
                    AIMessage(content=replacement, id=getattr(message, "id", None))
                    if item is message
                    else item
                    for item in response.result
                ]
            except Exception:
                logger.warning("自检失败后标注答案失败", exc_info=True)
        return response
