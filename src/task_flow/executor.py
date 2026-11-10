"""⑥ 执行器：逐执行单元推进执行计划。

步骤状态机：
    pending → running → checking(仅 checkpoint) → done
                  │           │
                  └──→ retrying(≤2) → degraded(主 Agent 兜底) → failed(上报)

- 步骤上下文最小化：instruction + constraints + example + 上游状态摘要。
- fork-join：每条分支默认委派 SubAgent（run_step 由调用方实现委派），
  并发上限 MAX_PARALLEL；join=all 时分支产出摘要注入下一单元。
- 中途接管：从 entry_step 启动，user_state 注入为上游状态（标记 user_provided）。
- HITL：执行器不做特殊分支，工具中断走 agent/hitl.py 现有契约。

执行器本身不直接调用 LLM——run_step / check_step 由集成层（agent 图）注入，
保证本模块可独立单测。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .models import (
    Execution,
    ExecutionPlan,
    ExecutionUnit,
    Step,
    StepTrace,
    TaskFlow,
    new_execution_id,
    utcnow,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_PARALLEL = 4
STEP_TIMEOUT_S = 600
# 完整步骤产出落库上限：防止单步异常长文本撑大执行记录。
EXECUTION_OUTPUT_MAX_CHARS = 20_000

# run_step(step, ctx) -> 步骤产出文本；ctx 含 upstream/example/executor/branch
RunStep = Callable[[Step, dict[str, Any]], Awaitable[str]]
# check_step(step, output, ctx) -> (是否合格, 反馈意见)
CheckStep = Callable[[Step, str, dict[str, Any]], Awaitable[tuple[bool, Optional[str]]]]
# 事件回调（SSE 进度）：(event_type, payload)
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class UnitResult:
    status: str  # done / failed / degraded
    output: str
    attempts: int
    duration_ms: int
    check_result: Optional[str] = None


class FlowExecutor:
    """执行计划解释器。"""

    def __init__(
        self,
        run_step: RunStep,
        check_step: Optional[CheckStep] = None,
        event_sink: Optional[EventSink] = None,
        *,
        max_retries: int = MAX_RETRIES,
        max_parallel: int = MAX_PARALLEL,
        step_timeout_s: int = STEP_TIMEOUT_S,
    ) -> None:
        self._run_step = run_step
        self._check_step = check_step
        self._event_sink = event_sink
        self._max_retries = max_retries
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._step_timeout_s = step_timeout_s

    async def execute(
        self,
        flow: TaskFlow,
        plan: ExecutionPlan,
        *,
        user_id: str,
        thread_id: str = "",
        entry_step: Optional[str] = None,
        user_state: str = "",
        trace_id: Optional[str] = None,
    ) -> Execution:
        """执行整个计划，返回执行记录（含全部 StepTrace）。"""
        execution = Execution(
            id=new_execution_id(),
            flow_id=flow.id,
            user_id=user_id,
            thread_id=thread_id,
            schema_hash=plan.schema_hash,
            entry_step=entry_step,
            trace_id=trace_id,
        )
        step_map = _build_step_map(flow)
        upstream = user_state  # 中途接管：用户产出作为初始上游状态
        started = entry_step is None
        state_source = "user_provided" if entry_step else "step"

        try:
            for unit in plan.units:
                if not started:
                    if unit.ref == entry_step:
                        started = True
                    else:
                        execution.step_traces.append(
                            self._trace(unit, None, "skipped", 0, 0, state_source=state_source)
                        )
                        continue
                result, branches_traces = await self._run_unit(
                    unit, step_map, upstream, state_source
                )
                execution.step_traces.extend(branches_traces)
                execution.step_traces.append(
                    self._trace(
                        unit, None, result.status, result.attempts, result.duration_ms,
                        input_summary=upstream[:200], output_summary=result.output[:200],
                        output=result.output, check_result=result.check_result,
                        state_source=state_source,
                    )
                )
                if result.status == "failed":
                    execution.status = "failed"
                    break
                upstream = result.output  # join 摘要作为下一单元上游状态
                state_source = "step"
            else:
                execution.status = "done"
        except Exception:
            logger.exception("任务流执行异常：flow=%s", flow.id)
            execution.status = "failed"
        execution.finished_at = utcnow()
        return execution

    # ----------------------------------------------------------
    # 单元执行
    # ----------------------------------------------------------

    async def _run_unit(
        self,
        unit: ExecutionUnit,
        step_map: dict[str, Step],
        upstream: str,
        state_source: str,
    ) -> tuple[UnitResult, list[StepTrace]]:
        if unit.type == "fork":
            return await self._run_fork(unit, step_map, upstream, state_source)
        step = step_map[unit.ref]
        result = await self._run_step_with_retry(step, unit, upstream, None)
        return result, []

    async def _run_fork(
        self,
        unit: ExecutionUnit,
        step_map: dict[str, Step],
        upstream: str,
        state_source: str,
    ) -> tuple[UnitResult, list[StepTrace]]:
        """fork-join：分支并行（默认 SubAgent 委派由 run_step 内部按 executor 处理）。"""
        branches = unit.branches or []

        async def run_branch(branch) -> tuple[str, UnitResult, list[StepTrace]]:
            async with self._semaphore:
                branch_upstream = upstream
                traces: list[StepTrace] = []
                for b_unit in branch.units:
                    step = step_map[b_unit.ref]
                    result = await self._run_step_with_retry(
                        step, b_unit, branch_upstream, branch.name
                    )
                    traces.append(
                        self._trace(
                            b_unit, branch.name, result.status, result.attempts,
                            result.duration_ms, input_summary=branch_upstream[:200],
                            output_summary=result.output[:200], output=result.output,
                            check_result=result.check_result, state_source=state_source,
                        )
                    )
                    if result.status == "failed":
                        return branch.name, UnitResult("failed", result.output, result.attempts, result.duration_ms), traces
                    branch_upstream = result.output
                # 分支最终产出即最后一步输出
                return branch.name, UnitResult("done", branch_upstream, 1, 0), traces

        results = await asyncio.gather(*(run_branch(b) for b in branches))
        all_traces = [t for _, _, traces in results for t in traces]
        failed = [name for name, r, _ in results if r.status == "failed"]
        if failed:
            return UnitResult("failed", f"并行分支失败：{'、'.join(failed)}", 1, 0), all_traces
        # join=all：各分支产出摘要拼接注入下一单元（只注摘要不灌全文）
        join_summary = "\n".join(
            f"【{name}】{r.output[:500]}" for name, r, _ in results
        )
        return UnitResult("done", join_summary, 1, 0), all_traces

    async def _run_step_with_retry(
        self,
        step: Step,
        unit: ExecutionUnit,
        upstream: str,
        branch: Optional[str],
    ) -> UnitResult:
        ctx = {
            "upstream": upstream,
            "example": step.example,
            "executor": step.executor,
            "branch": branch,
            "feedback": None,
        }
        attempts = 0
        start = time.monotonic()
        last_output = ""
        check_note: Optional[str] = None

        async def on_delta(delta: str) -> None:
            """步骤内 token 增量：执行器不关心来源，统一转成 step_token 事件。"""
            if delta:
                await self._emit(
                    "step_token",
                    {
                        "step_id": step.id,
                        "branch": branch,
                        "attempt": attempts,
                        "delta": delta,
                    },
                )

        ctx["on_delta"] = on_delta

        while attempts <= self._max_retries:
            attempts += 1
            if attempts > 1:
                await self._emit(
                    "step_retry",
                    {"step_id": step.id, "branch": branch, "attempt": attempts},
                )
            await self._emit("step_started", {"step_id": step.id, "branch": branch, "attempt": attempts})
            try:
                output = await asyncio.wait_for(
                    self._run_step(step, ctx), timeout=self._step_timeout_s
                )
            except Exception as exc:
                logger.warning("步骤执行失败（第 %s 次）：%s - %s", attempts, step.id, exc)
                ctx["feedback"] = f"上次执行异常：{exc}"
                continue
            last_output = output

            if not (unit.checkpoint and self._check_step):
                await self._emit("step_done", {"step_id": step.id, "branch": branch})
                return UnitResult("done", output, attempts, _elapsed_ms(start))

            ok, note = await self._check_step(step, output, ctx)
            check_note = note
            if ok:
                await self._emit("step_done", {"step_id": step.id, "branch": branch})
                return UnitResult("done", output, attempts, _elapsed_ms(start), note)
            ctx["feedback"] = f"校验不合格：{note}"
            logger.info("步骤 %s 校验不合格（第 %s 次）：%s", step.id, attempts, note)

        # 重试耗尽 → 主 Agent 兜底一次（degraded）
        ctx["executor"] = "main"
        ctx["feedback"] = f"前 {self._max_retries + 1} 次未通过校验（{check_note}），请兜底完成"
        try:
            output = await asyncio.wait_for(
                self._run_step(step, ctx), timeout=self._step_timeout_s
            )
            await self._emit("step_degraded", {"step_id": step.id, "branch": branch})
            return UnitResult("degraded", output, attempts + 1, _elapsed_ms(start), check_note)
        except Exception as exc:
            await self._emit("step_failed", {"step_id": step.id, "branch": branch, "error": str(exc)})
            return UnitResult("failed", last_output, attempts + 1, _elapsed_ms(start), check_note)

    # ----------------------------------------------------------
    # 工具
    # ----------------------------------------------------------

    def _trace(
        self,
        unit: ExecutionUnit,
        branch: Optional[str],
        status: str,
        attempts: int,
        duration_ms: int,
        *,
        input_summary: str = "",
        output_summary: str = "",
        output: str = "",
        check_result: Optional[str] = None,
        state_source: str = "step",
    ) -> StepTrace:
        return StepTrace(
            seq=unit.seq,
            step_id=unit.ref,
            branch=branch,
            status=status,  # type: ignore[arg-type]
            attempts=attempts,
            input_summary=input_summary,
            output_summary=output_summary,
            output=output[:EXECUTION_OUTPUT_MAX_CHARS],
            duration_ms=duration_ms,
            check_result=check_result,
            state_source=state_source,  # type: ignore[arg-type]
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            try:
                await self._event_sink(event_type, payload)
            except Exception:
                logger.exception("执行事件推送失败：%s", event_type)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _build_step_map(flow: TaskFlow) -> dict[str, Step]:
    step_map: dict[str, Step] = {}

    def walk(steps: list[Step]) -> None:
        for step in steps:
            step_map[step.id] = step
            for sub in step.parallel or []:
                walk(sub.steps)

    walk(flow.steps)
    return step_map
