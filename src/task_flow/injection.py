"""任务流注入层：把命中的任务流封装成一条 HumanMsg，交给主 Agent 图执行。

与旧执行器方案的区别：

- 步骤规范作为用户消息的一部分进入图，Agent 拥有全量工具、沙箱与 HITL；
- 步骤进度由图状态 ``task_flow`` 维护，由 ``task_flow_step_update`` 工具写入，
  同时以 custom 事件推送前端执行卡片；
- 命中后不再有独立的执行器协程，主 Agent 图本身就是执行主体。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from .compiler import schema_hash
from .models import TaskFlow, new_execution_id, utcnow

# 执行者前缀："subagent:<角色名>" 指内置 SubAgent；"agent:<id>" 指 MyAgent 档案。
AGENT_EXECUTOR_PREFIX = "agent:"
SUBAGENT_EXECUTOR_PREFIX = "subagent:"


def agent_executor_ids(flow: TaskFlow) -> list[str]:
    """收集流程里以 ``agent:<id>`` 指定的 MyAgent id（含并行分支，去重）。"""
    ids: list[str] = []

    def walk(steps: Iterable[Any]) -> None:
        for step in steps:
            if getattr(step, "parallel", None):
                for sub in step.parallel:
                    walk(sub.steps)
                continue
            value = str(getattr(step, "executor", "") or "")
            if value.startswith(AGENT_EXECUTOR_PREFIX):
                agent_id = value[len(AGENT_EXECUTOR_PREFIX) :].strip()
                if agent_id and agent_id not in ids:
                    ids.append(agent_id)

    walk(flow.steps)
    return ids


# ============================================================
# 前端步骤视图 / 图状态
# ============================================================


def flow_steps_view(flow: TaskFlow) -> list[dict[str, Any]]:
    """展开成前端执行卡片需要的步骤列表（并行分支展开为独立条目）。"""
    items: list[dict[str, Any]] = []

    def walk(steps, branch: Optional[str]) -> None:
        for step in steps:
            if step.parallel:
                for sub in step.parallel:
                    walk(sub.steps, f"{branch}/{sub.name}" if branch else sub.name)
                continue
            items.append({"id": step.id, "name": step.name, "branch": branch})

    walk(flow.steps, None)
    return items


def build_flow_state(flow: TaskFlow, *, entry_step: Optional[str] = None) -> dict[str, Any]:
    """构造写入图状态的任务流快照。

    快照随 checkpoint 持久化：HITL 恢复后工具仍能读到流程与步骤，
    执行记录也用同一份 ``execution_id`` 做 upsert，避免恢复后重复建行。
    """
    return {
        "flow_id": flow.id,
        "flow_name": flow.name,
        "schema_hash": schema_hash(flow),
        "execution_id": new_execution_id(),
        "entry_step": entry_step,
        "status": "running",
        "current_step": "",
        "started_at": utcnow().isoformat(),
        "steps": [
            {
                "id": item["id"],
                "name": item["name"],
                "branch": item["branch"],
                "status": "pending",
                "summary": "",
                # 步骤起止时间由 task_flow_step_update 在开始/结束时写入。
                "started_at": "",
                "finished_at": "",
            }
            for item in flow_steps_view(flow)
        ],
    }


# ============================================================
# HumanMsg 封装
# ============================================================


def build_task_flow_spec(
    flow: TaskFlow,
    *,
    entry_step: Optional[str] = None,
    user_state: str = "",
    executor_names: Optional[Mapping[str, str]] = None,
) -> str:
    """把任务流渲染成注入用的规范文本。

    用户原文由调用方单独作为一条 HumanMessage 发送，这里**不再重复拼接**：
    两者虽然都是 HumanMessage，但前者是用户可见的原话，后者是内部执行规范。
    步骤内容原样写入，只补充执行约定：逐步上报、缺信息必须询问、校验点先自检。
    ``executor_names`` 把 ``agent:<id>`` 渲染成可读的 Agent 名称与 id。
    """
    sections = [
        "你可以按照如下任务流执行任务：",
        _render_flow(flow, executor_names or {}),
        _render_conventions(),
    ]
    if entry_step:
        sections.append(
            f"补充说明：用户已完成 {entry_step} 之前的步骤，请直接从该步骤开始执行，"
            "前置步骤不要重复执行。"
        )
    if user_state.strip():
        sections.append(f"用户已完成部分的产出：\n{user_state.strip()}")
    return "\n\n".join(part for part in sections if part)


def _render_flow(flow: TaskFlow, executor_names: Mapping[str, str]) -> str:
    lines = [f"# 任务流：{flow.name}"]
    if flow.description:
        lines.append(flow.description)
    lines.append("")
    lines.append("## 步骤")
    _render_steps(flow.steps, lines, prefix="", executor_names=executor_names)
    return "\n".join(lines)


def _render_steps(
    steps,
    lines: list[str],
    *,
    prefix: str,
    executor_names: Mapping[str, str],
) -> None:
    for index, step in enumerate(steps, start=1):
        label = f"{prefix}{index}"
        if step.parallel:
            lines.append(f"{label}. [{step.id}] {step.name}（并行分支，按分支分别执行）")
            for branch in step.parallel:
                lines.append(f"   - 分支「{branch.name}」：")
                _render_steps(
                    branch.steps,
                    lines,
                    prefix=f"{label}.",
                    executor_names=executor_names,
                )
            continue
        lines.append(f"{label}. [{step.id}] {step.name}")
        if step.instruction:
            lines.append(f"   - 指令：{step.instruction}")
        if step.constraints:
            lines.append(f"   - 限制条件：{step.constraints}")
        if isinstance(step.skills, list) and step.skills:
            lines.append(f"   - 优先使用技能：{'、'.join(step.skills)}")
        if step.executor.startswith(AGENT_EXECUTOR_PREFIX):
            agent_id = step.executor[len(AGENT_EXECUTOR_PREFIX) :].strip()
            if agent_id:
                name = executor_names.get(agent_id)
                target = f"{name}（agent_id：{agent_id}）" if name else agent_id
                lines.append(f"   - 建议委派子 Agent：{target}")
        elif step.executor.startswith(SUBAGENT_EXECUTOR_PREFIX):
            role = step.executor[len(SUBAGENT_EXECUTOR_PREFIX) :].strip()
            if role:
                lines.append(f"   - 建议委派子 Agent：{role}")
        if step.example:
            example = "\n".join(f"     {line}" for line in step.example.splitlines())
            lines.append(f"   - 参考样例：\n{example}")
        if step.checkpoint:
            lines.append("   - 校验点：上报 done 前先按指令与样例自检")


def _render_conventions() -> str:
    return "\n".join(
        [
            "## 执行约定",
            "1. 严格按上面的顺序执行，不要合并或跳过步骤。",
            "2. 开始某一步时调用 "
            '`task_flow_step_update(step_id="<步骤 id>", status="running")`；'
            "完成时调用 "
            '`task_flow_step_update(step_id="<步骤 id>", status="done", summary="<该步产出摘要>")`。',
            "3. 步骤缺少必需信息时（例如审查范围、目标文件未给出），"
            "必须调用 `request_additional_info` 或 `request_user_choice` 询问用户，"
            "禁止自行假设后继续。",
            "4. 校验点步骤先自检产出是否满足指令与样例，再上报 done。",
            "5. 最终回答直接给出任务结果，不要复述步骤清单。",
        ]
    )


__all__ = [
    "agent_executor_ids",
    "build_flow_state",
    "build_task_flow_spec",
    "flow_steps_view",
]
