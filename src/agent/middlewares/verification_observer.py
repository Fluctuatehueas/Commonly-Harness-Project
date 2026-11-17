"""V3 最终回答验证观测中间件。

在回答放行后执行：默认后台任务，不占用户等待时间；关闭异步时在
``aafter_agent`` 内同步执行（仅调试用）。无论哪种方式，都不改变回答。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from agent.config import VERIFICATION_ASYNC, VERIFICATION_MAX_EVIDENCE
from agent.verification.evidence import (
    collect_evidence,
    message_text,
    plan_items_from_text,
)
from agent.verification.runner import VerificationRunner, point_enabled
from agent.verification.types import (
    VerificationLevel,
    VerificationPoint,
    VerificationRequest,
)

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def _final_answer(messages: list[Any]) -> str:
    """取最后一条不含 tool_calls 的 AI 文本。"""
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        if list(getattr(message, "tool_calls", []) or []):
            continue
        text = message_text(message)
        if text:
            return text
    return ""


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) not in {"human", "user"}:
            continue
        text = message_text(message)
        if text:
            return text
    return ""


class VerificationObserverMiddleware(AgentMiddleware):
    """在每轮结束后观测最终回答。"""

    def __init__(self, runner: VerificationRunner) -> None:
        super().__init__()
        self._runner = runner

    async def aafter_agent(self, state: dict[str, Any], runtime: Any) -> None:
        try:
            if not point_enabled(VerificationPoint.V3_FINAL):
                return None
            messages = list((state or {}).get("messages", []) or [])
            answer = _final_answer(messages)
            if not answer:
                return None

            context = getattr(runtime, "context", None)
            user_id = str(getattr(context, "user_id", "") or "")
            thread_id = str(getattr(context, "thread_id", "") or "")
            run_id = str(getattr(context, "trajectory_run_id", "") or "") or thread_id
            plan = str(
                (state or {}).get("pdc_plan")
                or getattr(context, "pdc_plan", "")
                or ""
            )
            request = VerificationRequest(
                point=VerificationPoint.V3_FINAL,
                level=VerificationLevel.L0,
                target="final_answer",
                answer=answer,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
                user_text=_last_user_text(messages),
                plan=plan,
                plan_items=plan_items_from_text(plan),
                evidence=collect_evidence(
                    messages,
                    limit=VERIFICATION_MAX_EVIDENCE,
                ),
                pdc_outcome=str(getattr(context, "pdc_outcome", "") or ""),
                pdc_reason=str(
                    getattr(context, "evolution_verification_reason", "") or ""
                ),
                context=context,
                store=getattr(runtime, "store", None),
            )
        except Exception:  # noqa: BLE001 - 观测准备失败不影响主流程
            logger.warning("准备最终回答验证失败", exc_info=True)
            return None

        if VERIFICATION_ASYNC:
            self._spawn(self._runner.run(request), user_id)
            return None
        try:
            await self._runner.run(request)
        except Exception:  # noqa: BLE001
            logger.warning("最终回答验证失败", exc_info=True)
        return None

    @staticmethod
    def _spawn(coroutine: Any, user_id: str) -> None:
        task = asyncio.create_task(coroutine)
        _background_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            _background_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.warning("后台验证失败：user=%s", user_id, exc_info=True)

        task.add_done_callback(_done)


__all__ = ["VerificationObserverMiddleware"]
