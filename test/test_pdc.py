"""PDCA 闭环的纯单元测试：不依赖数据库、模型服务与沙箱。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from langchain.agents.middleware import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from agent.evolution.types import JudgeVerdict
from agent.middlewares.pdc_guard import PdcGuardMiddleware
from agent.schema import HarnessContext
from agent.tools.planning_tools import WorkItem, create_work_plan
from agent.verification import rule_check


def test_plan_written_to_context() -> None:
    context = SimpleNamespace(pdc_plan="")
    state: dict = {}
    runtime = SimpleNamespace(context=context, state=state)
    output = create_work_plan.func(
        goal="调研",
        tasks=[
            WorkItem(id="a", title="检索", owner="web_researcher"),
            WorkItem(id="b", title="汇总", owner="main", depends_on=["a"]),
        ],
        runtime=runtime,
    )
    payload = json.loads(output)
    assert payload["ok"] is True
    assert len(payload["waves"]) == 2
    assert context.pdc_plan == output
    assert state["pdc_plan"] == output


def test_rule_check_and_verdict() -> None:
    assert rule_check("")[0] is False
    assert rule_check("x" * 30)[0] is True
    assert JudgeVerdict(True, True, False, False).passed is True
    assert JudgeVerdict(True, True, False, False, plan_covered=False).passed is False


def test_tool_retry_uses_idempotency_key() -> None:
    class Args(BaseModel):
        idempotency_key: str = ""

    class FakeTool:
        name = "create_ticket"
        args_schema = Args

    calls: list[dict] = []

    async def handler(request: ToolCallRequest):
        calls.append(dict(request.tool_call["args"]))
        if len(calls) == 1:
            return ToolMessage(
                content="Error: upstream timeout 504",
                tool_call_id="1",
                status="error",
            )
        return ToolMessage(content="ok", tool_call_id="1", status="success")

    middleware = PdcGuardMiddleware(None)  # 工具重试不调用模型
    request = ToolCallRequest(
        tool_call={"name": "create_ticket", "args": {}, "id": "1"},
        tool=FakeTool(),
        state={},
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )
    result = asyncio.run(middleware.awrap_tool_call(request, handler))
    assert getattr(result, "status", "") == "success"
    assert len(calls) == 2
    assert "idempotency_key" not in calls[0]
    assert calls[1]["idempotency_key"]


def test_check_failure_triggers_correction_round() -> None:
    class FakeJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, prompt, config=None):
            self.calls += 1
            passed = self.calls > 1
            payload = {
                "complete": passed,
                "grounded": True,
                "contradiction": False,
                "needs_verification": False,
                "plan_covered": True,
                "reason": "" if passed else "缺少结论",
            }
            return SimpleNamespace(content=json.dumps(payload))

    judge_model = FakeJudge()
    middleware = PdcGuardMiddleware(judge_model, max_rounds=2)
    context = HarnessContext(user_id="u1", username="u1", thread_id="t1")
    messages = [HumanMessage(content="给个结论")]
    calls = 0

    async def handler(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        text = (
            "第一版回答，包含结论与数据 1，但缺少关键依据"
            if calls == 1
            else "修正后的回答，补充了依据与结论 2，可以交付"
        )
        return ModelResponse(result=[AIMessage(content=text)])

    request = ModelRequest(
        model=judge_model,
        messages=messages,
        system_message=SystemMessage(content="sys"),
        state={"messages": messages},
        runtime=SimpleNamespace(context=context),
    )
    response = asyncio.run(middleware.awrap_model_call(request, handler))
    answer = response.result[-1].content
    assert calls == 2
    assert answer.startswith("修正后的回答")
    assert context.pdc_round == 1
    assert context.pdc_outcome == "passed"
