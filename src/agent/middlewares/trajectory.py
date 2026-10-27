"""轨迹（Trajectory）记录中间件。

把一次 Agent run 中的过程性信息写进 LangGraph Store，供「轨迹」页面展示：

- 模型思考 / 决策：耗时、Token 用量、上下文占用
- 工具、技能、子 Agent 委派：参数、结果、耗时
- 人工介入（HITL）：请求内容与中断状态
- 每轮开始 / 结束：上下文快照与汇总

该中间件不修改 state，也不影响主流程；Store 写入失败只告警。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.config import MODEL_CONTEXT_LIMIT, TRAJECTORY_MAX_TEXT
from agent.trajectory_store import append_trajectory_event, truncate_text
from agent.tools.async_sandbox_tools import START_ASYNC_TASK_TOOL_NAME
from agent.tools.hitl_tool import request_additional_info


logger = logging.getLogger(__name__)

# 与前端 chat store 的分类保持一致，轨迹页按这些类别分行。
SKILL_TOOLS = {
    "download_skill",
    "assign_skill",
    "list_subagent_skills",
    "delete_subagent_skill",
    "update_subagent_skill",
}
DELEGATION_TOOLS = {"task", START_ASYNC_TASK_TOOL_NAME}
HITL_TOOL_NAMES = {request_additional_info.name}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _message_text(message: Any) -> str:
    """把消息内容归一化为文本，兼容字符串与多模态块列表。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) not in {"human", "user"}:
            continue
        return _message_text(message)
    return ""


def _usage_tokens(message: Any) -> dict[str, int]:
    """读取 LangChain 标准 usage_metadata，兼容部分模型的 token_usage。"""
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict) or not usage:
        metadata = getattr(message, "response_metadata", None) or {}
        usage = metadata.get("token_usage") if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        return {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    return {
        key: int(value)
        for key, value in {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }.items()
        if value is not None
    }


def _reasoning_text(message: Any) -> str:
    """提取 DeepSeek / 兼容模型的思考内容。"""
    additional = getattr(message, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = additional.get(key) if isinstance(additional, dict) else None
        if value:
            return str(value)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
        ]
        return "\n".join(parts)
    return ""


def _tool_args_text(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(args)


def _subagent_name(args: Any) -> str:
    """从委派工具参数中读取子 Agent 名称。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return ""
    if not isinstance(args, dict):
        return ""
    return str(args.get("subagent_type") or args.get("subagent_name") or "")


class TrajectoryMiddleware(AgentMiddleware):
    """只读地采集 Agent 过程并写入 Store，供轨迹页面回放。"""

    def __init__(
        self,
        *,
        context_limit: int = MODEL_CONTEXT_LIMIT,
        max_text: int = TRAJECTORY_MAX_TEXT,
    ) -> None:
        super().__init__()
        self._context_limit = max(int(context_limit or 0), 0)
        self._max_text = max(int(max_text or 0), 0)
        # run_id -> 统计 / 开始时间 / 最近一次上下文快照
        self._stats: dict[str, dict[str, int]] = {}
        self._started: dict[str, datetime] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        # context 缺失时的兜底：thread_id -> run_id
        self._thread_runs: dict[str, str] = {}

    # ---------- 身份与 run 标识 ----------

    def _identity(self, runtime: Runtime) -> tuple[Any, str, str]:
        context = getattr(runtime, "context", None)
        user_id = str(getattr(context, "user_id", "") or "")
        thread_id = str(getattr(context, "thread_id", "") or "")
        if not thread_id:
            try:
                config = get_config()
                thread_id = str(
                    (config.get("configurable", {}) or {}).get("thread_id", "") or ""
                )
            except Exception:
                thread_id = ""
        return context, user_id, thread_id

    def _run_id(self, context: Any, thread_id: str) -> str:
        current = str(getattr(context, "trajectory_run_id", "") or "")
        if not current:
            current = uuid4().hex
            try:
                setattr(context, "trajectory_run_id", current)
            except Exception:
                # context 不可写时退回按 thread 记录，不阻断主流程。
                current = self._thread_runs.setdefault(thread_id, current)
        return current

    def _context_snapshot(self, messages: list[Any], system_message: Any = None) -> dict[str, Any]:
        """计算当前上下文 Token 与占用比例。"""
        counted = list(messages)
        if system_message is not None:
            counted.append(system_message)
        try:
            used = int(count_tokens_approximately(messages=counted))
        except Exception:
            used = 0
        limit = self._context_limit
        pct = round(used / limit, 4) if limit > 0 else 0.0
        return {"used_tokens": used, "limit": limit, "pct": pct}

    def _bump(self, run_id: str, key: str) -> None:
        stats = self._stats.setdefault(run_id, {})
        stats[key] = stats.get(key, 0) + 1

    # ---------- Agent 生命周期 ----------

    async def abefore_agent(self, state: dict[str, Any], runtime: Runtime) -> None:
        """一轮开始：生成 run_id，记录初始上下文。"""
        context, user_id, thread_id = self._identity(runtime)
        if not user_id or not thread_id:
            return None
        run_id = self._run_id(context, thread_id)
        messages = list((state or {}).get("messages", []) or [])
        started = _now()
        self._started[run_id] = started
        self._stats[run_id] = {}
        snapshot = self._context_snapshot(messages)
        self._contexts[run_id] = snapshot
        await self._emit(
            runtime,
            run_id=run_id,
            event_type="run",
            label="开始处理",
            status="running",
            started=started,
            context=snapshot,
            preview=_last_user_text(messages),
        )
        return None

    async def aafter_agent(self, state: dict[str, Any], runtime: Runtime) -> None:
        """一轮结束：写入结束事件与本轮汇总。"""
        context, user_id, thread_id = self._identity(runtime)
        if not user_id or not thread_id:
            return None
        run_id = self._run_id(context, thread_id)
        started = self._started.get(run_id, _now())
        ended = _now()
        messages = list((state or {}).get("messages", []) or [])
        # 结束时的 state 可能已被摘要中间件改写；优先沿用最后一次模型调用的快照，
        # 保证「上下文峰值」与本轮实际调用一致。
        snapshot = self._contexts.get(run_id) or self._context_snapshot(messages)
        stats = self._stats.pop(run_id, {})
        summary = {
            "model_calls": stats.get("model", 0),
            "tool_calls": stats.get("tool", 0),
            "skill_calls": stats.get("skill", 0),
            "delegations": stats.get("delegation", 0),
            "hitl": stats.get("hitl", 0),
            "duration_ms": int((ended - started).total_seconds() * 1000),
        }
        await self._emit(
            runtime,
            run_id=run_id,
            event_type="run",
            label="本轮结束",
            status="done",
            started=started,
            ended=ended,
            context=snapshot,
            summary=summary,
        )
        self._started.pop(run_id, None)
        self._contexts.pop(run_id, None)
        self._thread_runs.pop(thread_id, None)
        return None

    # ---------- 模型调用 ----------

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> ModelResponse:
        """包住模型调用，记录耗时、Token 与上下文占用。"""
        context, user_id, thread_id = self._identity(request.runtime)
        run_id = self._run_id(context, thread_id) if thread_id else uuid4().hex
        started = _now()
        messages = list(request.messages or [])
        snapshot = self._context_snapshot(messages, request.system_message)
        self._contexts[run_id] = snapshot
        try:
            response = await handler(request)
        except Exception as exc:
            ended = _now()
            if thread_id:
                await self._emit(
                    request.runtime,
                    run_id=run_id,
                    event_type="model",
                    label="模型调用失败",
                    status="error",
                    started=started,
                    ended=ended,
                    context=snapshot,
                    result=truncate_text(exc, self._max_text),
                )
            raise

        result_messages = getattr(response, "result", None)
        if isinstance(result_messages, list):
            response_messages = result_messages
        else:
            response_messages = [response]
        ai_message = next(
            (message for message in reversed(response_messages) if hasattr(message, "tool_calls")),
            response_messages[-1] if response_messages else None,
        )
        ended = _now()
        tool_calls = list(getattr(ai_message, "tool_calls", []) or [])
        tool_names = [str(call.get("name", "")) for call in tool_calls if isinstance(call, dict)]
        content = _message_text(ai_message)
        label = (
            f"模型决策：{' / '.join(tool_names)}"
            if tool_names
            else ("模型生成回答" if content else "模型思考")
        )
        if thread_id:
            try:
                self._bump(run_id, "model")
                await self._emit(
                    request.runtime,
                    run_id=run_id,
                    event_type="model",
                    label=label,
                    status="done",
                    started=started,
                    ended=ended,
                    context=snapshot,
                    preview=truncate_text(content, min(self._max_text, 600)),
                    result=truncate_text(_reasoning_text(ai_message), self._max_text),
                    tokens=_usage_tokens(ai_message),
                    tool_names=tool_names,
                )
            except Exception:
                # 观测失败不能影响模型返回，否则监控中间件会变成故障点。
                logger.warning("记录模型轨迹失败", exc_info=True)
        return response

    # ---------- 工具调用 ----------

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """包住工具调用，记录参数、结果、耗时与分类。"""
        context, user_id, thread_id = self._identity(request.runtime)
        if not thread_id:
            return await handler(request)
        run_id = self._run_id(context, thread_id)
        call = request.tool_call or {}
        tool_name = str(call.get("name", "tool"))
        args = call.get("args", {})
        event_type = self._tool_event_type(tool_name)
        started = _now()
        try:
            result = await handler(request)
        except Exception as exc:
            ended = _now()
            interrupted = type(exc).__name__ in {"GraphInterrupt", "Interrupt"}
            self._bump(run_id, "hitl" if interrupted else event_type)
            await self._emit(
                request.runtime,
                run_id=run_id,
                event_type=event_type,
                label=self._tool_label(tool_name, args, event_type),
                status="interrupted" if interrupted else "error",
                started=started,
                ended=ended,
                tool_name=tool_name,
                tool_call_id=str(call.get("id", "")),
                subagent_name=_subagent_name(args),
                args=truncate_text(_tool_args_text(args), self._max_text),
                result=truncate_text(exc, self._max_text),
                context=self._contexts.get(run_id, {}),
            )
            raise

        ended = _now()
        try:
            self._bump(run_id, event_type)
            await self._emit(
                request.runtime,
                run_id=run_id,
                event_type=event_type,
                label=self._tool_label(tool_name, args, event_type),
                status="error" if getattr(result, "status", None) == "error" else "done",
                started=started,
                ended=ended,
                tool_name=tool_name,
                tool_call_id=str(call.get("id", "")),
                subagent_name=_subagent_name(args),
                args=truncate_text(_tool_args_text(args), self._max_text),
                result=truncate_text(self._tool_result_text(result), self._max_text),
                context=self._contexts.get(run_id, {}),
            )
        except Exception:
            # 与模型路径一致：轨迹失败不影响工具结果返回。
            logger.warning("记录工具轨迹失败", exc_info=True)
        return result

    # ---------- 内部工具 ----------

    @staticmethod
    def _tool_event_type(tool_name: str) -> str:
        if tool_name in SKILL_TOOLS:
            return "skill"
        if tool_name in DELEGATION_TOOLS:
            return "delegation"
        if tool_name in HITL_TOOL_NAMES:
            return "hitl"
        return "tool"

    def _tool_label(self, tool_name: str, args: Any, event_type: str) -> str:
        if event_type == "skill":
            return f"使用技能：{tool_name}"
        if event_type == "delegation":
            subagent = _subagent_name(args)
            return f"委派子任务：{subagent or tool_name}"
        if event_type == "hitl":
            if isinstance(args, dict):
                needed = str(args.get("information_needed", "")).strip()
                if needed:
                    return f"请求人工介入：{needed[:80]}"
            return "请求人工介入"
        return f"调用工具：{tool_name}"

    @staticmethod
    def _tool_result_text(result: Any) -> str:
        content = getattr(result, "content", None)
        if content is not None:
            return _message_text(result)
        update = getattr(result, "update", None)
        if update is not None:
            return _tool_args_text(update)
        return str(result or "")

    async def _emit(self, runtime: Runtime, **kwargs: Any) -> None:
        """轨迹写入是旁路能力：任何异常都不能影响 Agent 主流程。"""
        try:
            await self._emit_impl(runtime, **kwargs)
        except Exception:
            logger.warning(
                "记录轨迹事件失败：type=%s",
                kwargs.get("event_type"),
                exc_info=True,
            )

    async def _emit_impl(
        self,
        runtime: Runtime,
        *,
        run_id: str,
        event_type: str,
        label: str,
        status: str,
        started: datetime,
        ended: datetime | None = None,
        context: dict[str, Any] | None = None,
        preview: str = "",
        result: str = "",
        args: str = "",
        tokens: dict[str, int] | None = None,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_names: list[str] | None = None,
        subagent_name: str = "",
        summary: dict[str, Any] | None = None,
    ) -> None:
        _, user_id, thread_id = self._identity(runtime)
        if not user_id or not thread_id:
            return
        duration_ms = (
            int((ended - started).total_seconds() * 1000) if ended is not None else None
        )
        event = {
            "id": uuid4().hex,
            "run_id": run_id,
            "type": event_type,
            "label": label,
            "status": status,
            "start": _iso(started),
            "end": _iso(ended) if ended else None,
            "duration_ms": duration_ms,
            "context": context or {},
            "preview": preview,
            "result": result,
            "args": args,
            "tokens": tokens or {},
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_names": tool_names or [],
            "subagent_name": subagent_name,
            "source": "main",
            "summary": summary or {},
        }
        await append_trajectory_event(
            getattr(runtime, "store", None),
            user_id=user_id,
            thread_id=thread_id,
            event=event,
        )
