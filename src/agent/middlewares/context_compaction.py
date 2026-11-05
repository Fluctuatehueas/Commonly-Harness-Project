"""上下文压缩中间件。

在 deepagents 摘要能力之上补三件事：

1. 触发策略：按项目窗口计算软/硬水位，并结合可观察的轨迹信号延迟压缩；
2. 可观测：压缩前后通过 ``stream_writer`` 发 SSE 事件，并写入轨迹 Store；
3. 可评估：压缩结束时同步计算锚点保真、时机标签与成本回执（零模型调用）。

真实摘要与原文归档仍复用 deepagents 的 ``SummarizationMiddleware``，
避免重写历史归档与溢出兜底逻辑。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from deepagents.middleware.summarization import (
    SUMMARIZATION_EVENT_KEY,
    SummarizationMiddleware,
    SummarizationToolMiddleware,
)
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from agent.context.anchors import Anchor, extract_anchors, message_text, render_anchor_block
from agent.context.budget import ContextBudget, build_budget
from agent.context.evaluator import evaluate_compaction, judge_compaction_timing
from agent.config import (
    COMPACTION_SIGNING_KEY,
    CONTEXT_COMPACT_COOLDOWN,
    CONTEXT_COMPACT_ENABLED,
    CONTEXT_COMPACT_EVAL_ENABLED,
    CONTEXT_COMPACT_EVAL_JUDGE,
    CONTEXT_COMPACT_HEARTBEAT,
    CONTEXT_COMPACT_MAX_DEFER,
    CONTEXT_COMPACT_MIN_ROUNDS,
    CONTEXT_COMPACT_TIMEOUT,
)
from agent.trajectory_store import (
    append_trajectory_event,
    truncate_text,
    update_trajectory_event,
)

logger = logging.getLogger(__name__)

# 摘要生成期间把本次锚点传给 ``_acreate_summary``，避免再算一次。
_CURRENT_ANCHORS: ContextVar[list[Anchor] | None] = ContextVar(
    "context_compaction_anchors", default=None
)


class CompactionSummaryError(RuntimeError):
    """只表示摘要生成失败，用于与主模型调用异常区分。"""


# 摘要阶段需要向前端推送分阶段进度，writer 与 ID 通过 ContextVar 传进去。
_CURRENT_WRITER: ContextVar[Any] = ContextVar("context_compaction_writer", default=None)
_CURRENT_COMPACTION_ID: ContextVar[str] = ContextVar(
    "context_compaction_id", default=""
)
# 后台 LLM 时机裁判任务需持有强引用，避免被事件循环提前回收。
_JUDGE_TASKS: set[asyncio.Task[Any]] = set()
_AUTOTUNE_TASKS: set[asyncio.Task[Any]] = set()

# 完整性摘要只覆盖稳定字段；评估结果会被异步更新，不能参与哈希。
_INTEGRITY_FIELDS = (
    "compaction_id",
    "trigger",
    "scope",
    "compaction_result",
    "start",
    "end",
)


def _event_integrity(event: dict[str, Any]) -> dict[str, Any]:
    """为压缩回执生成 sha256 摘要，并可选用 HMAC 签名。"""
    core = {key: event.get(key) for key in _INTEGRITY_FIELDS}
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    signature = ""
    if COMPACTION_SIGNING_KEY:
        signature = hmac.new(
            COMPACTION_SIGNING_KEY.encode("utf-8"),
            digest.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return {
        "algo": "sha256",
        "hash": digest,
        "signature": signature,
        "signed": bool(COMPACTION_SIGNING_KEY),
    }


@dataclass
class _ThreadState:
    """按 thread 记录的轻量触发状态。"""

    calls: int = 0
    last_compact_call: int = -10_000
    defer_count: int = 0
    last_human_count: int = 0


@dataclass(frozen=True)
class _Decision:
    compact: bool
    forced: bool
    deferred: bool
    reason: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _context_value(context: Any, name: str, default: Any = "") -> Any:
    return getattr(context, name, default) if context is not None else default


def _count_humans(messages: list[Any]) -> int:
    return sum(
        1
        for message in messages
        if getattr(message, "type", None) in {"human", "user"}
    )


def _tool_loop_depth(messages: list[Any]) -> int:
    """最后一条用户消息之后，带工具调用的 AI 消息数量。"""
    depth = 0
    for message in messages:
        if getattr(message, "type", None) in {"human", "user"}:
            depth = 0
        elif getattr(message, "type", None) == "ai" and getattr(message, "tool_calls", None):
            depth += 1
    return depth


def _last_ai_has_tool_calls(messages: list[Any]) -> bool:
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            return bool(getattr(message, "tool_calls", None))
    return False


def _control_anchors(context: Any) -> list[str]:
    """把运行时控制状态转成锚点文本。"""
    values: list[str] = []
    hitl_summary = str(_context_value(context, "hitl_summary", "") or "")
    if hitl_summary:
        values.append(f"HITL 用户回复：{hitl_summary}")
    return values


def _emit(writer: Any, payload: dict[str, Any]) -> None:
    """向 custom stream 写入一条事件；失败不影响主流程。"""
    if not callable(writer):
        return
    try:
        writer(payload)
    except Exception:
        logger.warning("写入压缩 SSE 事件失败", exc_info=True)


def _budget_payload(budget: ContextBudget) -> dict[str, Any]:
    return budget.as_dict()


def _emit_phase(phase: str) -> None:
    """向前端推送压缩阶段，用于区分“压缩”和“评估”两步。"""
    writer = _CURRENT_WRITER.get()
    if writer is None:
        return
    _emit(
        writer,
        {
            "type": "compaction_progress",
            "compaction_id": _CURRENT_COMPACTION_ID.get(),
            "phase": phase,
        },
    )


async def _heartbeat_loop(
    writer: Any,
    compaction_id: str,
    phase_getter: Callable[[], str],
    started_mono: float,
) -> None:
    """摘要期间周期性发进度事件，维持 SSE 活跃并支撑前端断线检测。"""
    interval = max(int(CONTEXT_COMPACT_HEARTBEAT), 2)
    try:
        while True:
            await asyncio.sleep(interval)
            _emit(
                writer,
                {
                    "type": "compaction_progress",
                    "compaction_id": compaction_id,
                    "phase": phase_getter(),
                    "elapsed_ms": int((time.monotonic() - started_mono) * 1000),
                },
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("压缩心跳失败", exc_info=True)


class HarnessContextCompactionMiddleware(SummarizationMiddleware):
    """自动压缩 + 触发策略 + 事件 + 回执。"""

    def __init__(
        self,
        model: Any,
        backend: Any,
        *,
        budget: ContextBudget | None = None,
    ) -> None:
        resolved = budget or build_budget()
        # 显式 token 阈值：不再受模型 profile 的 1M 窗口影响。
        super().__init__(
            model=model,
            backend=backend,
            trigger=("tokens", resolved.soft),
            keep=("tokens", resolved.keep),
        )
        self._budget = resolved
        self._states: dict[str, _ThreadState] = {}
        # compaction_id -> 当前阶段 / 心跳任务
        self._phase_by_id: dict[str, str] = {}
        self._heartbeat_by_id: dict[str, asyncio.Task[Any]] = {}

    # ---------- 摘要产物：附加确定性锚点块 ----------

    async def _acreate_summary(self, messages_to_summarize: list[Any]) -> str:
        # 只对摘要模型调用加超时，不能把超时套到整个主模型调用上。
        self._set_phase("summarize")
        try:
            summary = await asyncio.wait_for(
                super()._acreate_summary(messages_to_summarize),
                timeout=CONTEXT_COMPACT_TIMEOUT,
            )
        except Exception as exc:
            raise CompactionSummaryError(f"{type(exc).__name__}: {exc}") from exc
        self._set_phase("verify")
        anchors = _CURRENT_ANCHORS.get()
        if anchors:
            block = render_anchor_block(anchors)
            if block and block not in summary:
                summary = f"{summary}\n\n{block}"
        return summary

    def _set_phase(self, phase: str) -> None:
        compaction_id = _CURRENT_COMPACTION_ID.get()
        if compaction_id:
            self._phase_by_id[compaction_id] = phase
        _emit_phase(phase)

    def _start_heartbeat(
        self,
        writer: Any,
        compaction_id: str,
        started_mono: float,
    ) -> None:
        self._phase_by_id[compaction_id] = "compacting"
        task = asyncio.create_task(
            _heartbeat_loop(
                writer,
                compaction_id,
                lambda: self._phase_by_id.get(compaction_id, "compacting"),
                started_mono,
            )
        )
        self._heartbeat_by_id[compaction_id] = task

    def _stop_heartbeat(self, compaction_id: str) -> None:
        task = self._heartbeat_by_id.pop(compaction_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._phase_by_id.pop(compaction_id, None)

    # ---------- 触发策略 ----------

    def _state_for(self, thread_id: str) -> _ThreadState:
        return self._states.setdefault(thread_id, _ThreadState())

    def _decide(
        self,
        *,
        request: ModelRequest,
        messages: list[Any],
        total: int,
        state: _ThreadState,
    ) -> _Decision:
        context = getattr(request.runtime, "context", None)
        human_count = _count_humans(messages)

        if not CONTEXT_COMPACT_ENABLED:
            return _Decision(False, False, False, "disabled")
        # 首次压缩前要求最少轮次；压缩后摘要会折叠历史用户消息，
        # 不能再用当前消息里的 human 数量判断，否则长任务无法二次压缩。
        if state.last_compact_call < 0 and human_count < CONTEXT_COMPACT_MIN_ROUNDS:
            return _Decision(False, False, False, "too_short")

        forced = total >= self._budget.hard
        if not forced and total < self._budget.soft:
            return _Decision(False, False, False, "below_soft")

        # HITL 等待期间禁止压缩，直到 ResumeChat 完成。
        hitl_status = str(_context_value(context, "hitl_status", "") or "")
        if hitl_status in {"pending", "interrupted", "waiting"}:
            return _Decision(False, False, False, "hitl_pending")

        if not forced:
            if state.calls - state.last_compact_call < CONTEXT_COMPACT_COOLDOWN:
                return _Decision(False, False, False, "cooldown")
            busy = _tool_loop_depth(messages) >= 3 or _last_ai_has_tool_calls(messages)
            if busy and state.defer_count < CONTEXT_COMPACT_MAX_DEFER:
                return _Decision(False, False, True, "busy")
            if busy:
                return _Decision(True, True, False, "defer_limit")
            return _Decision(True, False, False, "soft_watermark")

        return _Decision(True, True, False, "hard_watermark")

    # ---------- 模型调用包装 ----------

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        thread_id = str(_context_value(context, "thread_id", "") or "")
        state = self._state_for(thread_id or "anonymous")
        state.calls += 1

        effective = self._get_effective_messages(request)
        before = self._count_tokens(effective, request.system_message, request.tools)
        decision = self._decide(
            request=request,
            messages=effective,
            total=before,
            state=state,
        )
        if not decision.compact:
            if decision.deferred:
                state.defer_count += 1
                logger.info(
                    "[ContextCompaction] thread=%s defer=%s used=%d soft=%d",
                    thread_id,
                    decision.reason,
                    before,
                    self._budget.soft,
                )
            return await handler(request)

        cutoff = self._determine_cutoff_index(effective)
        if cutoff <= 0:
            return await handler(request)

        to_summarize, preserved = self._partition_messages(effective, cutoff)
        anchors = extract_anchors(effective, extra=_control_anchors(context))
        writer = getattr(request.runtime, "stream_writer", None)
        compaction_id = f"cp-{_now().timestamp():.0f}-{id(request) % 100000}"
        started = _now()
        started_mono = time.monotonic()

        _emit(
            writer,
            {
                "type": "compaction_start",
                "compaction_id": compaction_id,
                "thread_id": thread_id,
                "trigger_type": decision.reason,
                "forced": decision.forced,
                "used_tokens": before,
                "budget": _budget_payload(self._budget),
                "limit": self._budget.usable,
                "window": self._budget.window,
                "pct": round(before / self._budget.usable, 4)
                if self._budget.usable
                else 0.0,
            },
        )

        self._start_heartbeat(writer, compaction_id, started_mono)

        captured: dict[str, Any] = {}
        finished = False

        async def observing_handler(modified: ModelRequest) -> ModelResponse:
            """摘要与锚点完成后、主模型开始生成前收尾。

            压缩结束事件必须在此刻发出：若等主模型整轮回答返回再发，
            前端会把整段回答当成“压缩期间的输出”缓冲起来，压缩结束后
            一次性倾泻，表现为突然多出一大段内容。
            """
            nonlocal finished
            if finished:
                return await handler(modified)
            finished = True
            # 压缩结束：停止心跳，避免主模型生成期间继续发“压缩中”进度。
            self._stop_heartbeat(compaction_id)
            modified_messages = list(modified.messages or [])
            captured["messages"] = modified_messages
            captured["system"] = modified.system_message
            captured["tools"] = modified.tools
            after = self._count_tokens(
                modified_messages,
                modified.system_message,
                modified.tools,
            )
            skipped = len(modified_messages) >= len(effective) and after >= before - 1000
            duration_ms = int((time.monotonic() - started_mono) * 1000)

            _emit(
                writer,
                {
                    "type": "compaction_end",
                    "compaction_id": compaction_id,
                    "thread_id": thread_id,
                    "status": "skipped" if skipped else "done",
                    "trigger_type": decision.reason,
                    "before_tokens": before,
                    "after_tokens": after,
                    "saved_tokens": 0 if skipped else max(before - after, 0),
                    "limit": self._budget.usable,
                    "window": self._budget.window,
                    "duration_ms": duration_ms,
                    "degraded": False,
                },
            )
            if skipped:
                logger.info(
                    "[ContextCompaction] thread=%s 摘要未生效，跳过本次压缩", thread_id
                )
                return await handler(modified)

            deferred_count = state.defer_count
            state.last_compact_call = state.calls
            state.defer_count = 0
            summary_text = message_text(modified_messages[0]) if modified_messages else ""
            summary_tokens = (
                self._count_tokens([modified_messages[0]], None, None)
                if modified_messages
                else 0
            )
            recorded = await self._record(
                request=request,
                compaction_id=compaction_id,
                status="done",
                trigger_type=decision.reason,
                deferred=deferred_count,
                started=started,
                duration_ms=duration_ms,
                before=before,
                after=after,
                summary=summary_text,
                summary_tokens=summary_tokens,
                anchors=anchors,
                messages_before=len(effective),
                messages_after=len(modified_messages),
                cutoff=cutoff,
            )
            self._schedule_judge(
                recorded,
                summary=summary_text,
                trigger_type=decision.reason,
                deferred=deferred_count,
                before=before,
                after=after,
            )
            logger.info(
                "[ContextCompaction] thread=%s trigger=%s %d -> %d tokens (saved %d) anchors=%d",
                thread_id,
                decision.reason,
                before,
                after,
                max(before - after, 0),
                len(anchors),
            )
            return await handler(modified)

        token_handle = _CURRENT_ANCHORS.set(anchors)
        writer_handle = _CURRENT_WRITER.set(writer)
        id_handle = _CURRENT_COMPACTION_ID.set(compaction_id)
        try:
            return await super().awrap_model_call(request, observing_handler)
        except CompactionSummaryError as exc:
            logger.warning("上下文压缩失败，走兜底截断", exc_info=True)
            _emit(
                writer,
                {
                    "type": "compaction_error",
                    "compaction_id": compaction_id,
                    "thread_id": thread_id,
                    "message": f"{type(exc).__name__}: {exc}",
                    "fallback": "tail_truncate",
                },
            )
            return await self._fallback(
                request=request,
                handler=handler,
                anchors=anchors,
                preserved=preserved,
                budget=self._budget,
                writer=writer,
                compaction_id=compaction_id,
                thread_id=thread_id,
                started=started,
                started_mono=started_mono,
                trigger_type=decision.reason,
                deferred=state.defer_count,
                before=before,
                state=state,
            )
        finally:
            self._stop_heartbeat(compaction_id)
            _CURRENT_COMPACTION_ID.reset(id_handle)
            _CURRENT_WRITER.reset(writer_handle)
            _CURRENT_ANCHORS.reset(token_handle)

    async def _fallback(
        self,
        *,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        anchors: list[Anchor],
        preserved: list[Any],
        budget: ContextBudget,
        writer: Any,
        compaction_id: str,
        thread_id: str,
        started: datetime,
        started_mono: float,
        trigger_type: str,
        deferred: int,
        before: int,
        state: _ThreadState,
    ) -> ModelResponse:
        """摘要失败时只保留锚点与最近消息，保证任务可以继续。"""
        # 失败后进入冷却，避免每次模型调用都重复尝试摘要。
        self._stop_heartbeat(compaction_id)
        state.last_compact_call = state.calls
        state.defer_count = 0
        block = render_anchor_block(anchors)
        lead = HumanMessage(
            content=f"上下文压缩失败，以下为保留的锁定事实：\n{block}",
        )
        messages = [lead, *preserved]
        response = await handler(request.override(messages=messages))
        after = self._count_tokens(messages, request.system_message, request.tools)
        duration_ms = int((time.monotonic() - started_mono) * 1000)
        _emit(
            writer,
            {
                "type": "compaction_end",
                "compaction_id": compaction_id,
                "thread_id": thread_id,
                "status": "degraded",
                "trigger_type": trigger_type,
                "before_tokens": before,
                "after_tokens": after,
                "saved_tokens": max(before - after, 0),
                "limit": budget.usable,
                "window": budget.window,
                "duration_ms": duration_ms,
                "degraded": True,
            },
        )
        await self._record(
            request=request,
            compaction_id=compaction_id,
            status="degraded",
            trigger_type=trigger_type,
            deferred=deferred,
            started=started,
            duration_ms=duration_ms,
            before=before,
            after=after,
            summary=block,
            summary_tokens=self._count_tokens([lead], None, None),
            anchors=anchors,
            messages_before=0,
            messages_after=len(messages),
            cutoff=0,
        )
        return response

    # ---------- 轨迹回执 ----------

    async def _record(
        self,
        *,
        request: ModelRequest,
        compaction_id: str,
        status: str,
        trigger_type: str,
        deferred: int,
        started: datetime,
        duration_ms: int,
        before: int,
        after: int,
        summary: str,
        summary_tokens: int,
        anchors: list[Anchor],
        messages_before: int,
        messages_after: int,
        cutoff: int,
    ) -> dict[str, Any] | None:
        context = getattr(request.runtime, "context", None)
        store = getattr(request.runtime, "store", None)
        user_id = str(_context_value(context, "user_id", "") or "")
        thread_id = str(_context_value(context, "thread_id", "") or "")
        run_id = str(_context_value(context, "trajectory_run_id", "") or thread_id)
        if not user_id or not thread_id:
            return None

        evaluation = evaluate_compaction(
            anchors=anchors,
            summary=summary,
            before_tokens=before,
            after_tokens=after,
            summary_tokens=summary_tokens,
            trigger_type=trigger_type,
            deferred=deferred,
            duration_ms=duration_ms,
        ) if CONTEXT_COMPACT_EVAL_ENABLED else {"status": "skipped"}

        history_path = ""
        history_root = getattr(self, "_history_path_prefix", "")
        match = re.search(r"([\w./-]+conversation_history[\w./-]*\.md)", summary)
        if match:
            history_path = match.group(1)
        elif history_root:
            history_path = history_root

        event = {
            "id": f"{compaction_id}:evt",
            "run_id": run_id,
            "type": "compaction",
            "label": "上下文压缩",
            "status": status,
            "start": _iso(started),
            "end": _iso(_now()),
            "duration_ms": duration_ms,
            "context": {
                "used_tokens": after,
                "limit": self._budget.window,
                "pct": round(after / self._budget.window, 4)
                if self._budget.window
                else 0.0,
            },
            "compaction_id": compaction_id,
            "budget": _budget_payload(self._budget),
            "trigger": {
                "type": trigger_type,
                "used_tokens": before,
                "limit": self._budget.usable,
                "pct": round(before / self._budget.usable, 4)
                if self._budget.usable
                else 0.0,
                "signals": {"defer_count": deferred},
            },
            "scope": {
                "cutoff_index": cutoff,
                "messages_before": messages_before,
                "messages_after": messages_after,
                "layers": ["history"],
            },
            "compaction_result": {
                "before_tokens": before,
                "after_tokens": after,
                "saved_tokens": max(before - after, 0),
                "summary_tokens": summary_tokens,
                "summary_preview": truncate_text(summary, 600),
                "history_file": history_path,
                "anchors_total": len(anchors),
            },
            "evaluation": evaluation,
            "schema_version": "1.0",
        }
        event["integrity"] = _event_integrity(event)
        try:
            await append_trajectory_event(
                store,
                user_id=user_id,
                thread_id=thread_id,
                event=event,
            )
        except Exception:
            logger.warning("写入压缩轨迹失败", exc_info=True)
            return None

        from agent.context import autotune

        if autotune.record(evaluation):
            task = asyncio.create_task(autotune.maybe_apply())
            _AUTOTUNE_TASKS.add(task)
            task.add_done_callback(_AUTOTUNE_TASKS.discard)
        return {**event, "_store": store, "_user_id": user_id, "_thread_id": thread_id}

    def _schedule_judge(
        self,
        recorded: dict[str, Any] | None,
        *,
        summary: str,
        trigger_type: str,
        deferred: int,
        before: int,
        after: int,
    ) -> None:
        """异步 LLM 时机裁判；失败时保留规则标签。"""
        if not recorded or not CONTEXT_COMPACT_EVAL_JUDGE:
            return
        task = asyncio.create_task(
            self._judge_and_update(
                recorded,
                summary=summary,
                trigger_type=trigger_type,
                deferred=deferred,
                before=before,
                after=after,
            )
        )
        _JUDGE_TASKS.add(task)
        task.add_done_callback(_JUDGE_TASKS.discard)

    async def _judge_and_update(
        self,
        recorded: dict[str, Any],
        *,
        summary: str,
        trigger_type: str,
        deferred: int,
        before: int,
        after: int,
    ) -> None:
        verdict = await judge_compaction_timing(
            self.model,
            summary=summary,
            trigger_type=trigger_type,
            deferred=deferred,
            before_tokens=before,
            after_tokens=after,
            window=self._budget.window,
        )
        if not verdict:
            return
        store = recorded.get("_store")
        user_id = str(recorded.get("_user_id") or "")
        thread_id = str(recorded.get("_thread_id") or "")
        event = {
            key: value
            for key, value in recorded.items()
            if not key.startswith("_")
        }
        evaluation = dict(event.get("evaluation") or {})
        evaluation["rule_timing"] = evaluation.get("timing", "")
        evaluation["timing"] = verdict["timing"]
        evaluation["judge_status"] = "done"
        evaluation["reason"] = (
            f"LLM 裁判：{verdict['reason']}（规则标签：{evaluation['rule_timing']}）"
        )
        event["evaluation"] = evaluation
        await update_trajectory_event(
            store,
            user_id=user_id,
            thread_id=thread_id,
            event=event,
        )


class HarnessCompactionToolMiddleware(SummarizationToolMiddleware):
    """手动 ``compact_conversation`` 的观测包装。"""

    @staticmethod
    def _tool_text(result: Any) -> str:
        update = getattr(result, "update", None) or {}
        messages = update.get("messages") if isinstance(update, dict) else None
        if isinstance(messages, list) and messages:
            return message_text(messages[-1])
        return ""

    async def _arun_compact(self, runtime: Any) -> Any:
        if not CONTEXT_COMPACT_ENABLED:
            return await super()._arun_compact(runtime)

        summarization = self._summarization
        state_messages = list(runtime.state.get("messages", []) or [])
        event = runtime.state.get(SUMMARIZATION_EVENT_KEY)
        effective = summarization._apply_event_to_messages(state_messages, event)
        cutoff = summarization._determine_cutoff_index(effective)
        if cutoff <= 0:
            return await super()._arun_compact(runtime)

        to_summarize, preserved = summarization._partition_messages(effective, cutoff)
        context = runtime.context
        anchors = extract_anchors(effective, extra=_control_anchors(context))
        before = summarization._count_tokens(effective, None, None)
        started = _now()
        started_mono = time.monotonic()
        compaction_id = f"cp-manual-{int(started.timestamp())}"
        budget = getattr(summarization, "_budget", build_budget())
        _emit(
            runtime.stream_writer,
            {
                "type": "compaction_start",
                "compaction_id": compaction_id,
                "thread_id": str(_context_value(context, "thread_id", "") or ""),
                "trigger_type": "manual",
                "forced": False,
                "used_tokens": before,
                "budget": _budget_payload(budget),
                "limit": budget.usable,
                "window": budget.window,
                "pct": round(before / budget.usable, 4) if budget.usable else 0.0,
            },
        )

        token_handle = _CURRENT_ANCHORS.set(anchors)
        writer_handle = _CURRENT_WRITER.set(runtime.stream_writer)
        id_handle = _CURRENT_COMPACTION_ID.set(compaction_id)
        try:
            result = await super()._arun_compact(runtime)
        finally:
            _CURRENT_COMPACTION_ID.reset(id_handle)
            _CURRENT_WRITER.reset(writer_handle)
            _CURRENT_ANCHORS.reset(token_handle)

        update = getattr(result, "update", None) or {}
        summary_message = None
        if isinstance(update, dict):
            event_value = update.get(SUMMARIZATION_EVENT_KEY)
            if isinstance(event_value, dict):
                summary_message = event_value.get("summary_message")
        status = "done" if summary_message is not None else "skipped"
        summary_text = message_text(summary_message) if summary_message else ""
        after = (
            summarization._count_tokens([summary_message, *preserved], None, None)
            if summary_message
            else before
        )
        duration_ms = int((time.monotonic() - started_mono) * 1000)
        if status == "done":
            _emit(
                runtime.stream_writer,
                {
                    "type": "compaction_end",
                    "compaction_id": compaction_id,
                    "thread_id": str(_context_value(context, "thread_id", "") or ""),
                    "status": status,
                    "trigger_type": "manual",
                    "before_tokens": before,
                    "after_tokens": after,
                    "saved_tokens": max(before - after, 0),
                    "limit": budget.usable,
                    "window": budget.window,
                    "duration_ms": duration_ms,
                    "degraded": False,
                },
            )
        else:
            _emit(
                runtime.stream_writer,
                {
                    "type": "compaction_end",
                    "compaction_id": compaction_id,
                    "thread_id": str(_context_value(context, "thread_id", "") or ""),
                    "status": status,
                    "trigger_type": "manual",
                    "before_tokens": before,
                    "after_tokens": before,
                    "saved_tokens": 0,
                    "limit": budget.usable,
                    "window": budget.window,
                    "duration_ms": duration_ms,
                    "degraded": False,
                },
            )
            return result

        user_id = str(_context_value(context, "user_id", "") or "")
        thread_id = str(_context_value(context, "thread_id", "") or "")
        run_id = str(_context_value(context, "trajectory_run_id", "") or thread_id)
        evaluation = evaluate_compaction(
            anchors=anchors,
            summary=summary_text,
            before_tokens=before,
            after_tokens=after,
            summary_tokens=summarization._count_tokens([summary_message], None, None),
            trigger_type="manual",
            deferred=0,
            duration_ms=duration_ms,
        ) if CONTEXT_COMPACT_EVAL_ENABLED else {"status": "skipped"}
        if user_id and thread_id:
            manual_event = {
                "id": f"{compaction_id}:evt",
                "run_id": run_id,
                "type": "compaction",
                "label": "手动压缩",
                "status": status,
                "start": _iso(started),
                "end": _iso(_now()),
                "duration_ms": duration_ms,
                "context": {
                    "used_tokens": after,
                    "limit": budget.window,
                    "pct": round(after / budget.window, 4)
                    if budget.window
                    else 0.0,
                },
                "compaction_id": compaction_id,
                "budget": _budget_payload(budget),
                "trigger": {
                    "type": "manual",
                    "used_tokens": before,
                    "limit": budget.usable,
                    "pct": round(before / budget.usable, 4)
                    if budget.usable
                    else 0.0,
                    "signals": {},
                },
                "scope": {
                    "cutoff_index": cutoff,
                    "messages_before": len(effective),
                    "messages_after": len(preserved) + 1,
                    "layers": ["history"],
                },
                "compaction_result": {
                    "before_tokens": before,
                    "after_tokens": after,
                    "saved_tokens": max(before - after, 0),
                    "summary_preview": truncate_text(summary_text, 600),
                    "history_file": getattr(
                        summarization, "_history_path_prefix", ""
                    ),
                    "anchors_total": len(anchors),
                },
                "evaluation": evaluation,
                "schema_version": "1.0",
            }
            manual_event["integrity"] = _event_integrity(manual_event)
            try:
                await append_trajectory_event(
                    runtime.store,
                    user_id=user_id,
                    thread_id=thread_id,
                    event=manual_event,
                )
            except Exception:
                logger.warning("写入手动压缩轨迹失败", exc_info=True)

            from agent.context import autotune

            if autotune.record(evaluation):
                task = asyncio.create_task(autotune.maybe_apply())
                _AUTOTUNE_TASKS.add(task)
                task.add_done_callback(_AUTOTUNE_TASKS.discard)
        return result


__all__ = [
    "HarnessContextCompactionMiddleware",
    "HarnessCompactionToolMiddleware",
]
