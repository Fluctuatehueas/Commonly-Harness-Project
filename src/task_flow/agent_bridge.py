"""任务流与 Agent 运行时的桥接层。

实现 executor.py 依赖注入的两个回调：

- ``run_step``：按 ``ctx["executor"]`` 分发执行主体——
  ``main`` / 主流 ``auto`` 用主 Agent 模型直接完成；
  ``subagent:<角色名>`` 用 ``aload_subagent`` 加载 YAML 配置委派对应 SubAgent；
  fork 分支的 ``auto`` 步骤默认委派通用 SubAgent（提升子智能体委派率）。
- ``check_step``：规则检查（非空、长度下限）+ 有 ``example`` 时让轻量模型
  对照示例评审格式 / 结构 / 关键要素。

另提供对话入口集成所需的 ``route_task_flow`` / ``execute_task_flow``。

agent 与 api 依赖全部延迟导入：``src/task_flow`` 被独立导入时不拖入
Agent 运行时，保持模块可独立单测。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from .executor import EventSink, FlowExecutor
from .models import Execution, Step
from .router import FlowRouter, RouteDecision

logger = logging.getLogger(__name__)

# 步骤产出长度下限（字符）；低于此视为未完成，触发 checkpoint 重试。
MIN_OUTPUT_CHARS = 20
# fork 分支 auto 步骤默认委派的通用 SubAgent 角色名
# （配置见 src/agent/subagents/configs/general_purpose.yaml）。
GENERIC_SUBAGENT_ROLE = "general_purpose"

# 已加载的子 Agent 图缓存：MCP 工具绑定昂贵，同一角色只加载一次。
_SUBAGENT_GRAPHS: dict[str, Any] = {}


# ============================================================
# run_step：步骤执行
# ============================================================


def build_step_prompt(step: Step, ctx: dict[str, Any]) -> str:
    """组装步骤 prompt：指令 + 限制条件 + 示例 + 上游摘要 + 重试反馈。

    步骤上下文保持最小化（设计文档 5.2）：短指令遵循度更好，也控制上下文体积。
    """
    parts = [f"当前步骤：{step.name}"]
    if step.instruction:
        parts.append(f"任务指令：{step.instruction}")
    if step.constraints:
        parts.append(f"限制条件：{step.constraints}")
    if step.example:
        parts.append(f"参考样例（产出的格式 / 结构 / 风格应对照此样例）：\n{step.example}")
    upstream = str(ctx.get("upstream") or "").strip()
    if upstream:
        parts.append(f"上游状态（前序步骤的产出摘要）：\n{upstream}")
    feedback = str(ctx.get("feedback") or "").strip()
    if feedback:
        parts.append(f"返工反馈（上次产出未通过校验，请据此修正）：\n{feedback}")
    parts.append("请只输出本步骤的产出内容，不要输出多余解释。")
    return "\n\n".join(parts)


async def run_step(step: Step, ctx: dict[str, Any]) -> str:
    """执行单个步骤并返回产出文本（签名与 executor.RunStep 一致）。

    ``ctx["on_delta"]`` 存在时，主模型走流式输出，token 增量实时回调；
    SubAgent 仍为一次性返回（图内流式输出的中间文本会污染最终产出）。
    """
    prompt = build_step_prompt(step, ctx)
    executor = str(ctx.get("executor") or "auto").strip()
    on_delta = ctx.get("on_delta")
    if executor == "main":
        return await _run_with_main_model(prompt, on_delta)
    if executor.startswith("subagent:"):
        role = executor.split(":", 1)[1].strip()
        if role:
            return await _run_with_subagent(role, prompt)
        logger.warning("步骤 %s 的 executor 未指定角色名，回退主模型执行", step.id)
        return await _run_with_main_model(prompt, on_delta)
    # auto：fork 分支步骤默认委派通用 SubAgent；主流步骤由主模型执行。
    if ctx.get("branch"):
        return await _run_with_subagent(GENERIC_SUBAGENT_ROLE, prompt)
    return await _run_with_main_model(prompt, on_delta)


async def _run_with_main_model(
    prompt: str,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """用主 Agent 模型直接完成步骤（纯文本产出，不挂工具）。

    传入 ``on_delta`` 时使用 ``astream``，把每个文本增量交给执行器；
    回调异常只记日志，不影响步骤本身完成。
    """
    from agent.config import get_main_model

    messages = [
        {
            "role": "system",
            "content": "你是任务流执行器中的步骤执行体，严格按用户给出的步骤指令产出内容。",
        },
        {"role": "user", "content": prompt},
    ]
    model = get_main_model()
    if on_delta is None:
        response = await model.ainvoke(messages)
        return _content_to_text(getattr(response, "content", response))

    parts: list[str] = []
    async for chunk in model.astream(messages):
        text = _content_to_text(getattr(chunk, "content", chunk))
        if not text:
            continue
        parts.append(text)
        try:
            await on_delta(text)
        except Exception:
            logger.exception("步骤 token 增量回调失败")
    return "".join(parts)


async def _run_with_subagent(role: str, prompt: str) -> str:
    """按角色名加载 SubAgent YAML 配置，创建子 Agent 图并委派执行。"""
    graph = await _get_subagent_graph(role)
    result = await graph.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    return _extract_last_text(result)


async def _get_subagent_graph(role: str) -> Any:
    """加载并缓存指定角色的子 Agent 图（按用户隔离）。"""
    from agent.identity import current_user_id

    cache_key = f"{current_user_id()}:{role}"
    if cache_key in _SUBAGENT_GRAPHS:
        return _SUBAGENT_GRAPHS[cache_key]
    from agent.config import get_main_model
    from agent.subagents.configs import config_path
    from agent.subagents.loader import aload_subagent
    from agent.tools.commom_tools import get_basic_tools
    from agent.tools.document_tools import read_word_document
    from deepagents import create_deep_agent

    # 本地工具作为可选工具的解析来源；MCP 工具由 aload_subagent 按声明绑定。
    config = await aload_subagent(
        config_path(f"{role}.yaml"),
        local_tools=[*get_basic_tools(), read_word_document],
    )
    graph = create_deep_agent(
        model=config.get("model") or get_main_model(),
        system_prompt=config["system_prompt"],
        tools=config["tools"],
        checkpointer=None,
        name=f"task_flow_{role}",
    )
    _SUBAGENT_GRAPHS[cache_key] = graph
    return graph


# ============================================================
# check_step：checkpoint 校验
# ============================================================


async def check_step(
    step: Step, output: str, ctx: dict[str, Any]
) -> tuple[bool, Optional[str]]:
    """轻量校验：规则检查 + 有 example 时的模型对照评审。

    规则检查不调用模型；示例对照评审用摘要模型（低成本、输出稳定）。
    """
    text = (output or "").strip()
    if not text:
        return False, "产出为空"
    if len(text) < MIN_OUTPUT_CHARS:
        return False, f"产出过短（{len(text)} 字符），疑似未完成"
    if step.example:
        return await _review_against_example(step, text)
    return True, None


async def _review_against_example(step: Step, output: str) -> tuple[bool, Optional[str]]:
    """让轻量模型对照 example 检查产出的格式 / 结构 / 关键要素。"""
    from agent.config import get_summary_model

    prompt = (
        "你是任务流步骤产出的质量评审。请对照「参考样例」检查「步骤产出」的格式、结构"
        "与关键要素是否齐备；只关注形式符合度，不评价内容优劣。\n"
        "首行只输出结论：PASS 或 FAIL。若为 FAIL，第二行起用一句话说明缺失点，"
        "这句话会作为返工反馈交给执行者。\n\n"
        f"【步骤名称】{step.name}\n"
        f"【步骤指令】{step.instruction}\n"
        f"【参考样例】\n{step.example}\n\n"
        f"【步骤产出】\n{output}"
    )
    try:
        response = await get_summary_model().ainvoke([{"role": "user", "content": prompt}])
    except Exception:
        # 评审模型不可用时不阻断流程，记日志后按通过处理。
        logger.exception("步骤 %s 示例对照评审失败，按通过处理", step.id)
        return True, None
    verdict = _content_to_text(getattr(response, "content", response)).strip()
    first_line, _, rest = verdict.partition("\n")
    if first_line.strip().upper().startswith("PASS"):
        return True, None
    return False, rest.strip() or "产出与参考样例的格式 / 结构 / 关键要素不符"


# ============================================================
# 对话入口集成：路由前置 + 执行并落库
# ============================================================


async def route_task_flow(
    message: str,
    user_id: str,
    *,
    flow_id: Optional[str] = None,
    entry_step: Optional[str] = None,
    user_state: str = "",
) -> Optional[RouteDecision]:
    """对话入口的任务流路由前置查询。

    命中（hit / takeover）返回 RouteDecision；未命中（miss / suggest）返回 None。
    任何路由异常都降级为 None——任务流不可用时不能影响主 Agent 流程。
    """
    try:
        from api.task_flow import get_store

        store = await get_store()
        decision = await FlowRouter(store).route(
            message,
            user_id,
            flow_id=flow_id,
            entry_step=entry_step,
            user_state=user_state,
        )
    except Exception:
        logger.exception("任务流路由失败，降级为通用 Agent 流程")
        return None
    return decision if decision.hit else None


async def execute_task_flow(
    decision: RouteDecision,
    *,
    user_id: str,
    thread_id: str = "",
    event_sink: Optional[EventSink] = None,
    trace_id: Optional[str] = None,
) -> tuple[Execution, str]:
    """执行任务流并把执行记录落库，返回（执行记录, 最终产出文本）。"""
    assert decision.flow is not None and decision.plan is not None
    outputs: dict[str, str] = {}

    async def _record_run(step: Step, ctx: dict[str, Any]) -> str:
        # 包装 run_step 以留存各步骤完整产出，用于汇总最终回答
        # （执行记录里只存 200 字摘要，不能直接当回答）。
        output = await run_step(step, ctx)
        outputs[step.id] = output
        return output

    executor = FlowExecutor(
        run_step=_record_run, check_step=check_step, event_sink=event_sink
    )
    execution = await executor.execute(
        decision.flow,
        decision.plan,
        user_id=user_id,
        thread_id=thread_id,
        entry_step=decision.entry_step,
        user_state=decision.user_state,
        trace_id=trace_id,
    )
    try:
        from api.task_flow import get_store

        store = await get_store()
        await store.save_execution(execution)
    except Exception:
        # 落库失败不吞掉本次执行结果，只记日志。
        logger.exception("任务流执行记录落库失败：%s", execution.id)
    return execution, _final_output(execution, outputs)


def _final_output(execution: Execution, outputs: dict[str, str]) -> str:
    """汇总本轮最终产出文本。

    取最后一个完成的主流单元的产出；若最后单元是 fork，则按 executor 的
    join 摘要格式（【分支名】产出前 500 字）重建汇合结果。
    """
    if execution.status != "done":
        return ""
    main_traces = [t for t in execution.step_traces if t.branch is None]
    last = next(
        (t for t in reversed(main_traces) if t.status in ("done", "degraded")), None
    )
    if last is None:
        return ""
    if last.step_id in outputs:
        return outputs[last.step_id]
    branch_last: dict[str, str] = {}
    for trace in execution.step_traces:
        if trace.branch and trace.step_id in outputs:
            branch_last[trace.branch] = outputs[trace.step_id]
    return "\n".join(f"【{name}】{text[:500]}" for name, text in branch_last.items())


# ============================================================
# 文本提取工具
# ============================================================


def _content_to_text(content: Any) -> str:
    """把模型返回的 content（字符串或内容块列表）转成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _extract_last_text(result: Any) -> str:
    """从子 Agent 图结果中提取最后一条助手消息的文本。"""
    messages = result.get("messages") if isinstance(result, dict) else None
    if not messages:
        return _content_to_text(getattr(result, "content", ""))
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
    return _content_to_text(content)
