"""③ 流编译器：schema → 执行计划（纯函数）。

拉平步骤为执行单元数组；fork 单元内联分支的单元数组（递归编译）。
同输入必同输出，直接表驱动单测。
"""

from __future__ import annotations

import hashlib
import json

from .models import ExecutionPlan, ExecutionUnit, ForkBranch, PLAN_VERSION, Step, TaskFlow


def schema_hash(flow: TaskFlow) -> str:
    """canonical JSON 的 sha256：schema 任何字段变化都会改变 hash。"""
    canonical = json.dumps(
        flow.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compile_steps(steps: list[Step], start_seq: int) -> tuple[list[ExecutionUnit], int]:
    units: list[ExecutionUnit] = []
    seq = start_seq
    for step in steps:
        if step.parallel:
            branches = []
            for sub in step.parallel:
                branch_units, seq = _compile_steps(sub.steps, seq)
                branches.append(ForkBranch(name=sub.name, units=branch_units))
            units.append(
                ExecutionUnit(
                    seq=seq,
                    type="fork",
                    ref=step.id,
                    executor=step.executor,
                    checkpoint=step.checkpoint,
                    branches=branches,
                    join="all",
                )
            )
            seq += 1
        else:
            units.append(
                ExecutionUnit(
                    seq=seq,
                    type="step",
                    ref=step.id,
                    executor=step.executor,
                    checkpoint=step.checkpoint,
                )
            )
            seq += 1
    return units, seq


def compile(flow: TaskFlow) -> ExecutionPlan:
    """编译任务流为执行计划。"""
    units, _ = _compile_steps(flow.steps, start_seq=1)
    return ExecutionPlan(flow_id=flow.id, schema_hash=schema_hash(flow), units=units)
