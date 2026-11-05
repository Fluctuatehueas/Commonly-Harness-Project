"""每轮结束后的演进编排：奖励、经验、归因与候选。

编排在后台任务中执行，避免阻塞最终回答的 ``done`` 事件。所有失败只
告警，不影响主流程。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.runtime import Runtime

from agent.config import (
    EVOLUTION_ENABLED,
    EVOLUTION_TENANT_ID,
)
from agent.evolution.attribution import (
    attribute_failure,
    list_attributions,
    save_attribution,
)
from agent.evolution.control_plane import (
    propose_candidate,
    _save_candidate,
)
from agent.evolution.experience import (
    ExperienceStore,
    extract_experiences,
    save_experiences,
)
from agent.evolution.reward import append_reward
from agent.evolution.types import InterventionLayer, RunSignals

logger = logging.getLogger(__name__)

_CORRECTION_TOKENS = (
    "不对",
    "错了",
    "不是",
    "重来",
    "重新",
    "纠正",
    "again",
    "wrong",
    "not what",
)
_background_tasks: set[asyncio.Task[Any]] = set()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _is_final_answer(messages: list[Any]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    if getattr(last, "type", None) != "ai":
        return False
    if list(getattr(last, "tool_calls", []) or []):
        return False
    return bool(_message_text(last))


def _current_run_messages(messages: list[Any]) -> list[Any]:
    """只分析本轮用户消息之后的工具消息，避免历史失败反复触发学习。"""
    start = 0
    for index, message in enumerate(messages):
        if getattr(message, "type", None) in {"human", "user"}:
            start = index
    return messages[start:]


def _collect_tool_errors(messages: list[Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for message in _current_run_messages(messages):
        if getattr(message, "type", None) != "tool":
            continue
        status = str(getattr(message, "status", "") or "")
        content = _message_text(message)
        if status != "error" and not content.startswith("Error:"):
            continue
        errors.append(
            {
                "tool": str(getattr(message, "name", "") or ""),
                "status": status or "error",
                "content": content[:500],
            }
        )
    return errors


def _detect_user_correction(messages: list[Any]) -> bool:
    humans = [
        _message_text(message)
        for message in messages
        if getattr(message, "type", None) in {"human", "user"}
    ]
    if len(humans) < 2:
        return False
    latest = humans[-1].lower()
    return any(token in latest for token in _CORRECTION_TOKENS)


def _repeated_steps(messages: list[Any]) -> int:
    names: list[str] = []
    for message in _current_run_messages(messages):
        for call in list(getattr(message, "tool_calls", []) or []):
            if isinstance(call, dict) and call.get("name"):
                names.append(str(call["name"]))
    seen: dict[str, int] = {}
    max_repeat = 0
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        max_repeat = max(max_repeat, seen[name])
    return max_repeat


def _build_signals(messages: list[Any], context: Any) -> RunSignals:
    tool_errors = _collect_tool_errors(messages)
    verification_failed = bool(
        getattr(context, "evolution_verification_failed", False)
    )
    hitl_rejected = str(getattr(context, "hitl_status", "") or "") == "rejected"
    user_correction = _detect_user_correction(messages)
    repeated = _repeated_steps(messages)
    injected_ids = [
        item
        for item in str(
            getattr(context, "evolution_experience_ids", "") or ""
        ).split(",")
        if item
    ]
    signal = RunSignals(
        has_failure=bool(
            tool_errors
            or verification_failed
            or hitl_rejected
            or user_correction
            or repeated >= 2
        ),
        tool_errors=tool_errors,
        verification_failed=verification_failed,
        hitl_rejected=hitl_rejected,
        user_correction=user_correction,
        repeated_steps=repeated,
        injected_experience_ids=injected_ids,
    )
    return signal


async def _persist_evolution(
    *,
    model: BaseChatModel,
    store: Any,
    user_id: str,
    tenant_id: str,
    thread_id: str,
    execution_id: str,
    messages: list[Any],
    context: Any,
) -> None:
    signals = _build_signals(messages, context)
    experience_store = ExperienceStore(store, user_id)

    # 经验注入后的结果反馈：成功则提升效用，失败则衰减。
    for experience_id in signals.injected_experience_ids:
        delta = -0.2 if signals.has_failure else 0.1
        await experience_store.update_utility(experience_id, delta=delta)

    if not signals.has_failure:
        return

    evidence_refs = [f"trajectory/{execution_id}"]
    # 工具级奖励已由 TrajectoryMiddleware 写入；这里只补一条 run 级失败信号，
    # 避免同一个工具错误被重复计分。
    await append_reward(
        store,
        user_id=user_id,
        thread_id=thread_id,
        tenant_id=tenant_id,
        execution_id=execution_id,
        event_id=f"{execution_id}:run",
        source="run_signal",
        value=-0.2,
        category="task_failure",
        confidence=0.6,
        evidence=(
            f"tool_errors={len(signals.tool_errors)}, "
            f"verification_failed={signals.verification_failed}, "
            f"hitl_rejected={signals.hitl_rejected}, "
            f"user_correction={signals.user_correction}"
        ),
        delayed=True,
    )

    items = await extract_experiences(model, messages=messages, signals=signals)
    saved = await save_experiences(
        experience_store,
        items,
        user_id=user_id,
        evidence_refs=evidence_refs,
        signals=signals,
    )

    attribution = await attribute_failure(
        model,
        user_id=user_id,
        thread_id=thread_id,
        execution_id=execution_id,
        signals=signals,
        evidence_refs=evidence_refs,
    )
    await save_attribution(store, user_id, thread_id, attribution)

    if attribution.layer == InterventionLayer.NOOP or attribution.high_uncertainty:
        return
    previous = await list_attributions(store, user_id=user_id, thread_id=thread_id)
    failure_count = sum(
        1
        for item in previous
        if item.signal == attribution.signal and not item.high_uncertainty
    )
    candidate = await propose_candidate(
        store,
        user_id=user_id,
        tenant_id=tenant_id,
        attribution=attribution,
        failure_count=failure_count,
    )
    if candidate is not None and saved:
        candidate.change_spec["experience_id"] = saved[0].experience_id
        await _save_candidate(store, candidate)


class EvolutionMiddleware(AgentMiddleware):
    """在最终回答后异步执行演进编排。"""

    def __init__(self, model: BaseChatModel) -> None:
        super().__init__()
        self._model = model

    async def aafter_agent(self, state: dict[str, Any], runtime: Runtime) -> None:
        if not EVOLUTION_ENABLED:
            return None
        context = getattr(runtime, "context", None)
        user_id = str(getattr(context, "user_id", "") or "")
        thread_id = str(getattr(context, "thread_id", "") or "")
        store = getattr(runtime, "store", None)
        if not user_id or not thread_id or store is None:
            return None
        messages = list((state or {}).get("messages", []) or [])
        if not _is_final_answer(messages):
            return None
        tenant_id = str(getattr(context, "tenant_id", "") or EVOLUTION_TENANT_ID or "default")
        execution_id = str(getattr(context, "trajectory_run_id", "") or thread_id)

        coroutine = _persist_evolution(
            model=self._model,
            store=store,
            user_id=user_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            execution_id=execution_id,
            messages=messages,
            context=context,
        )
        self._spawn(coroutine, user_id)
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
            except Exception:
                logger.warning("演进编排失败：user=%s", user_id, exc_info=True)

        task.add_done_callback(_done)
