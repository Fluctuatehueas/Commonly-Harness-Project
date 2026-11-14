"""PDCA 闭环中间件。

在现有 Agent 图上实现 Plan → Do → Check → Act，不额外包一层图：

* Plan：``create_work_plan`` 把分工清单写进运行时上下文；
* Do：模型正常调用工具 / 子 Agent；
* Check：最终回答返回前做规则检查、L1 模型自检、L2 确定性验证与计划完成度校验；
* Act：未通过时把原因和计划注入下一轮模型调用；模型可以重新调用工具补齐证据，
  也可以直接给出修正后的回答。

同一轮的重做次数由 ``HARNESS_PDCA_MAX_ROUNDS`` 控制，达到上限后只记录失败信号，
不修改用户可见回答。工具调用失败时，仅在工具声明了 ``idempotency_key`` 参数时
自动补键并重试一次，避免对非幂等副作用重复执行。
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from agent.config import (
    EVOLUTION_ENABLED,
    EVOLUTION_JUDGE_ENABLED,
    EVOLUTION_JUDGE_RISK_TIER,
    EVOLUTION_MODE,
    PDCA_ENABLED,
    PDCA_MAX_ROUNDS,
)
from agent.evolution.judge import SelfJudge
from agent.evolution.types import RiskTier
from agent.evolution.verifiers import build_default_verifiers
from agent.verification import rule_check

logger = logging.getLogger(__name__)

HIGH_RISK_TOOLS = {
    "write_file",
    "edit_file",
    "host_write_file",
    "host_execute",
    "publish_to_project",
    "run_in_sandbox",
    "compile_project",
}

_CORRECTION_BLOCK = (
    "\n\n## 输出前自检未通过（第 {round} 轮）\n"
    "原因：{reason}\n"
    "{plan}"
    "上一版回答（仅供参考，可整体重写）：\n{answer}\n"
    "请核对是否还有未完成的工作项或未验证的事实。需要时重新调用工具补充证据，"
    "然后给出修正后的最终回答；不要重复已经成功的操作，也不要编造未验证的信息。"
)

_PLAN_BLOCK = "当前计划：\n{plan}\n"

_TRANSIENT_TOKENS = (
    "timeout",
    "timed out",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "rate limit",
    "temporarily",
    "busy",
    "broken pipe",
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


def _current_plan(
    state: dict[str, Any],
    context: Any,
    messages: list[Any],
) -> str:
    """本轮的用户消息从上下文取计划；HITL 恢复时回退到图状态里的计划。"""
    last_type = getattr(messages[-1], "type", "") if messages else ""
    if last_type not in {"human", "user"} and isinstance(state, dict):
        plan = str(state.get("pdc_plan") or "")
        if plan:
            return plan
    return str(getattr(context, "pdc_plan", "") or "")


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


def _is_tool_error(result: Any) -> bool:
    if getattr(result, "status", None) == "error":
        return True
    return _message_text(result).startswith("Error:")


def _is_transient_tool_error(result: Any) -> bool:
    """只对瞬时错误做幂等重试，避免重复执行参数/业务错误。"""
    text = _message_text(result).lower()
    return any(token in text for token in _TRANSIENT_TOKENS)


class PdcGuardMiddleware(AgentMiddleware):
    """在最终回答返回前执行 Check，并在失败时驱动带反馈的 Act 轮次。"""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        max_rounds: int | None = None,
    ) -> None:
        super().__init__()
        self._judge = SelfJudge(model)
        self._threshold = self._parse_threshold(EVOLUTION_JUDGE_RISK_TIER)
        configured = PDCA_MAX_ROUNDS if max_rounds is None else max_rounds
        self._max_rounds = max(0, min(int(configured), 5))

    @staticmethod
    def _parse_threshold(value: str) -> RiskTier:
        try:
            return RiskTier(value)
        except ValueError:
            return RiskTier.T1

    def _judging_enabled(self) -> bool:
        return (
            PDCA_ENABLED
            and EVOLUTION_ENABLED
            and EVOLUTION_JUDGE_ENABLED
            and EVOLUTION_MODE != "observe"
        )

    def _enabled_for(self, tier: RiskTier) -> bool:
        if not self._judging_enabled():
            return False
        order = {RiskTier.T0: 0, RiskTier.T1: 1, RiskTier.T2: 2}
        return order[tier] >= order[self._threshold]

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> None:
        """每轮开始重置运行时 PDCA 状态；计划本身存放在图状态中。"""
        context = getattr(runtime, "context", None)
        if context is None:
            return None
        try:
            context.pdc_plan = ""
            context.pdc_round = 0
            context.pdc_feedback = ""
            context.pdc_outcome = ""
            context.pdc_max_rounds = self._max_rounds
        except Exception:
            logger.debug("PDCA 上下文初始化失败", exc_info=True)
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """工具失败时，仅在可幂等的前提下自动重试一次。"""
        result = await handler(request)
        if not PDCA_ENABLED or not _is_tool_error(result):
            return result
        if not _is_transient_tool_error(result):
            return result

        tool_call = dict(getattr(request, "tool_call", None) or {})
        args = dict(tool_call.get("args") or {})
        key = str(args.get("idempotency_key") or "").strip()
        if not key:
            schema = getattr(getattr(request, "tool", None), "args_schema", None)
            fields = getattr(schema, "model_fields", {}) or {}
            if "idempotency_key" not in fields:
                return result
            key = uuid.uuid4().hex
            args["idempotency_key"] = key
            tool_call["args"] = args
            request = dataclasses.replace(request, tool_call=tool_call)

        logger.info("工具调用失败，使用幂等键重试一次: %s", tool_call.get("name"))
        return await handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        if not self._judging_enabled():
            # 自检关闭时同样要放行：正文一直缓冲到 done 会表现为「没有流式输出」。
            self._release(request, "disabled")
            return response

        candidate = _should_judge(response)
        if candidate is None:
            return response

        context = getattr(request.runtime, "context", None)
        state = getattr(request, "state", None) or {}
        tier = _risk_tier(state, candidate[1])
        if not self._enabled_for(tier):
            # 风险等级低于阈值（如无工具调用的 T0 闲聊）不跑自检，直接放行。
            self._release(request, "below_tier", tier)
            return response

        user_text = _user_text(list(getattr(request, "messages", []) or []))
        plan = _current_plan(
            state,
            context,
            list(getattr(request, "messages", []) or []),
        )
        max_rounds = max(
            0,
            min(
                int(
                    getattr(context, "pdc_max_rounds", self._max_rounds)
                    or self._max_rounds
                ),
                5,
            ),
        )
        local_round = 0

        while True:
            answer = candidate[1]
            ok, reason = await self._verify(
                answer=answer,
                user_text=user_text,
                plan=plan,
                context=context,
            )
            if ok:
                self._mark(context, "passed")
                # 告知前端「验证已通过、正文可以渲染」：正文在验证期间一直缓冲，
                # 这里放行后前端才看到完整回答，避免草稿闪现后被撤回。
                self._emit(
                    getattr(request, "runtime", None),
                    {"type": "pdc_verified", "tier": tier},
                )
                return response

            used = (
                int(getattr(context, "pdc_round", 0) or 0)
                if context is not None
                else local_round
            )
            if used >= max_rounds:
                response = self._record_verification_failure(
                    response=response,
                    reason=reason,
                    tier=tier,
                    context=context,
                )
                # 重做次数用尽仍要放行原文，否则用户要等到 done 才看到回答。
                self._release(request, "exhausted", tier)
                return response

            local_round += 1
            if context is not None:
                try:
                    context.pdc_round = used + 1
                    context.pdc_feedback = reason
                except Exception:
                    logger.debug("PDCA 轮次写入失败", exc_info=True)

            corrected = await self._retry_with_correction(
                request=request,
                handler=handler,
                reason=reason,
                answer=answer,
                plan=plan,
                round_no=used + 1,
            )
            if corrected is None:
                response = self._record_verification_failure(
                    response=response,
                    reason=reason,
                    tier=tier,
                    context=context,
                )
                self._release(request, "correction_failed", tier)
                return response
            response = corrected
            next_candidate = _should_judge(response)
            if next_candidate is None:
                # 模型选择继续调用工具：让图继续执行，下一次最终回答会重新进入检查。
                self._mark(context, "acting")
                return response
            candidate = next_candidate

    async def _verify(
        self,
        *,
        answer: str,
        user_text: str,
        plan: str,
        context: Any,
    ) -> tuple[bool, str]:
        """规则检查 + L1 自检 + L2 验证，返回（是否通过, 失败原因）。"""
        ok, note = rule_check(answer, min_chars=1)
        if not ok:
            return False, str(note or "产出未通过规则检查")

        verdict = await self._judge.judge_l1(
            user_text=user_text,
            answer=answer,
            plan=plan,
        )
        failed = (
            not verdict.complete
            or not verdict.grounded
            or verdict.contradiction
            or (bool(plan) and not verdict.plan_covered)
        )
        if failed:
            reason = verdict.reason or "自检判定回答不完整、不扎实或存在矛盾"
            if plan and not verdict.plan_covered:
                reason = f"回答未覆盖计划中的全部工作项：{reason}"
            return False, reason

        if verdict.needs_verification:
            verifiers = build_default_verifiers(context)
            results = await self._judge.judge_l2(
                answer=answer,
                verifiers=verifiers,
                context=context,
            )
            failed = [result for result in results if not result.passed]
            if failed:
                return False, "; ".join(result.detail for result in failed)
        return True, ""

    async def _retry_with_correction(
        self,
        *,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        reason: str,
        answer: str,
        plan: str,
        round_no: int,
    ) -> ModelResponse | None:
        self._emit(
            getattr(request, "runtime", None),
            {
                "type": "pdc_revision",
                "round": round_no,
                "reason": reason,
            },
        )
        try:
            system_message = request.system_message or SystemMessage(content="")
            content = system_message.content
            block = _CORRECTION_BLOCK.format(
                round=round_no,
                reason=reason,
                plan=_PLAN_BLOCK.format(plan=plan) if plan else "",
                answer=answer[:1500],
            )
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
            logger.warning("PDCA 修正轮失败", exc_info=True)
            return None

    @staticmethod
    def _emit(runtime: Any, payload: dict[str, Any]) -> None:
        """向 SSE 自定义事件流发一条 PDCA 事件；不可用时静默跳过。"""
        writer = getattr(runtime, "stream_writer", None)
        if not callable(writer):
            return
        try:
            writer(payload)
        except Exception:
            logger.debug("PDCA 事件发送失败", exc_info=True)

    @staticmethod
    def _release(request: ModelRequest, reason: str, tier: Any = None) -> None:
        """放行已缓冲的正文。

        只有真正执行并通过自检时才需要延迟渲染；跳过自检、重做次数用尽或
        修正调用失败时，必须显式放行，否则正文会一直缓冲到本轮结束。
        """
        payload: dict[str, Any] = {"type": "pdc_verified", "released_by": reason}
        if tier is not None:
            payload["tier"] = tier
        PdcGuardMiddleware._emit(getattr(request, "runtime", None), payload)

    @staticmethod
    def _mark(context: Any, outcome: str) -> None:
        if context is None:
            return
        try:
            context.pdc_outcome = outcome
        except Exception:
            pass

    @staticmethod
    def _record_verification_failure(
        *,
        response: ModelResponse,
        reason: str,
        tier: RiskTier,
        context: Any,
    ) -> ModelResponse:
        """记录自检失败，但不改动用户可见回答。"""
        if context is not None:
            try:
                context.evolution_verification_failed = True
                context.evolution_verification_reason = reason
                context.evolution_risk_tier = tier.value
                context.pdc_outcome = "exhausted"
            except Exception:
                pass
        preview = " ".join(str(reason or "").split())[:180] or "自检未通过"
        logger.warning("PDCA 自检未通过：tier=%s，原因：%s", tier.value, preview)
        return response
