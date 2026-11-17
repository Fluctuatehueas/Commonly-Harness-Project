"""V1 / V2 验证点观测中间件。

只读取工具结果并写验证记录，不修改结果、不阻断调用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest

from agent.config import VERIFICATION_ASYNC
from agent.verification import budget
from agent.verification.evidence import DELEGATION_TOOLS, HIGH_RISK_TOOLS
from agent.verification.l0 import DATA_TOOLS
from agent.verification.runner import VerificationRunner, point_enabled
from agent.verification.types import (
    VerificationLevel,
    VerificationPoint,
    VerificationRequest,
)

logger = logging.getLogger(__name__)

V1_TOOLS = HIGH_RISK_TOOLS | DATA_TOOLS
V2_TOOLS = DELEGATION_TOOLS

_background_tasks: set[asyncio.Task[Any]] = set()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _result_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if content is not None:
        return _message_text(result)
    update = getattr(result, "update", None)
    if update is not None:
        try:
            return json.dumps(update, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(update)
    return str(result or "")


def _args_text(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(args)


class VerificationPointMiddleware(AgentMiddleware):
    """在工具返回后观察 V1 / V2，并把结论写入轨迹。"""

    def __init__(self, runner: VerificationRunner) -> None:
        super().__init__()
        self._runner = runner

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> None:
        # 每轮重置预算计数；计数器只存在进程内上下文。
        budget.reset(getattr(runtime, "context", None))
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Any,
    ) -> Any:
        result = await handler(request)
        try:
            await self._observe(request, result)
        except Exception:  # noqa: BLE001 - 验证失败不影响工具结果
            logger.warning("工具验证观测失败", exc_info=True)
        return result

    async def _observe(self, request: ToolCallRequest, result: Any) -> None:
        tool_call = dict(getattr(request, "tool_call", None) or {})
        name = str(tool_call.get("name") or "")
        if not name:
            return
        args = tool_call.get("args")
        target = name
        user_text = ""
        is_delegation = name in V2_TOOLS
        if is_delegation:
            if isinstance(args, dict):
                target = str(
                    args.get("subagent_type")
                    or args.get("subagent_name")
                    or name
                )
                user_text = str(
                    args.get("description") or args.get("prompt") or ""
                )
        elif name not in V1_TOOLS:
            return

        point = (
            VerificationPoint.V2_SUBAGENT
            if is_delegation
            else VerificationPoint.V1_TOOL
        )
        if not point_enabled(point):
            return

        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        store = getattr(runtime, "store", None)
        user_id = str(getattr(context, "user_id", "") or "")
        thread_id = str(getattr(context, "thread_id", "") or "")
        run_id = str(getattr(context, "trajectory_run_id", "") or "") or thread_id
        status = str(getattr(result, "status", "") or "")
        text = _result_text(result)

        verification_request = VerificationRequest(
            point=point,
            level=VerificationLevel.L0,
            target=target,
            answer=text,
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            user_text=user_text,
            tool_name=name,
            tool_args=_args_text(args),
            tool_result=text,
            tool_status=status,
            tool_call_id=str(tool_call.get("id") or ""),
            context=context,
            store=store,
        )
        # V2 可能抽样调用判官；放后台执行，避免拖慢委派结果返回。
        if is_delegation and VERIFICATION_ASYNC:
            self._spawn(self._runner.run(verification_request))
            return
        await self._runner.run(verification_request)

    @staticmethod
    def _spawn(coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        _background_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            _background_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.warning("后台委派验证失败", exc_info=True)

        task.add_done_callback(_done)


__all__ = ["V1_TOOLS", "V2_TOOLS", "VerificationPointMiddleware"]
