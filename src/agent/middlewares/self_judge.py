"""输出前两层自我裁定中间件。

L1 使用独立摘要模型做结构化自检；L2 使用确定性验证器。只对最终回答
生效，避免每次模型调用都增加固定开销。验证失败时有限重试一次；仍失败
只记录内部失败信号，不改动用户可见回答。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

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

        _, answer = candidate
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
                _, retry_answer = retry_candidate
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
                response = corrected

        # 自检结果只留在内部信号里，绝不能拼进用户可见回答。
        return self._record_verification_failure(
            response=response,
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
    def _record_verification_failure(
        *,
        response: ModelResponse,
        reason: str,
        tier: RiskTier,
        context: Any,
    ) -> ModelResponse:
        """记录自检失败，但不改动用户可见回答。

        失败信号供演进模块做奖励、归因与经验抽取；回答本身原样交付，
        避免把「自检提示 / 校验原因」暴露给用户。
        """
        try:
            setattr(context, "evolution_verification_failed", True)
            setattr(context, "evolution_verification_reason", reason)
            setattr(context, "evolution_risk_tier", tier.value)
        except Exception:
            pass
        preview = " ".join(str(reason or "").split())[:180] or "自检未通过"
        logger.warning("自检未通过：tier=%s，原因：%s", tier.value, preview)
        return response
