"""上下文压缩单元自检（无需 pytest）。

运行：python test/context_eval/test_units.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.context.anchors import extract_anchors, anchor_fidelity, render_anchor_block  # noqa: E402
from agent.context.budget import build_budget  # noqa: E402
from agent.context.evaluator import evaluate_compaction  # noqa: E402
from agent.context.autotune import recommend  # noqa: E402
from agent.middlewares.context_compaction import (  # noqa: E402
    HarnessContextCompactionMiddleware,
    _ThreadState,
    _event_integrity,
)
from agent.schema import HarnessContext  # noqa: E402


def test_budget_numbers() -> None:
    budget = build_budget(128000)
    assert budget.window == 128000
    assert budget.reserve == 15360
    assert budget.usable == 112640
    assert budget.soft == 78848
    assert budget.hard == 99123
    assert budget.keep == 28160


def test_budget_small_window() -> None:
    budget = build_budget(32000)
    assert budget.usable > 0
    assert budget.soft < budget.hard
    assert budget.keep < budget.soft


def test_anchor_extraction_and_fidelity() -> None:
    messages = [
        HumanMessage(content="第一轮：先看日志 /workspace/logs/app.log"),
        AIMessage(content="已经确认结论：需要先检查 E500。task_id=abc123"),
        HumanMessage(content="继续：按上面的结论，把这份报告改成分段排查，不要动数据库。"),
    ]
    anchors = extract_anchors(messages)
    kinds = {anchor.kind for anchor in anchors}
    assert "goal" in kinds
    assert any("app.log" in anchor.value for anchor in anchors)
    assert any(anchor.kind == "identifier" for anchor in anchors)

    block = render_anchor_block(anchors)
    assert "锁定事实" in block
    kept, total = anchor_fidelity(block, anchors)
    assert total > 0 and kept == total


def test_evaluator_timing() -> None:
    anchors = extract_anchors([HumanMessage(content="目标：整理 /workspace/report.md")])
    summary = render_anchor_block(anchors)
    result = evaluate_compaction(
        anchors=anchors,
        summary=summary,
        before_tokens=81234,
        after_tokens=34210,
        summary_tokens=968,
        trigger_type="hard",
        deferred=0,
        duration_ms=11000,
    )
    assert result["status"] == "done"
    assert result["timing"] == "forced"
    assert result["saved_tokens"] == 47024
    assert result["fidelity"] == 1.0


def _policy(*, humans: int = 8, hitl: str = "", busy: bool = False):
    """构造一个只包含 _decide 所需字段的假请求。"""
    from types import SimpleNamespace

    messages = [HumanMessage(content=f"第 {index} 轮任务输入") for index in range(humans)]
    if busy:
        messages.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {}, "id": "t"}],
            )
        )

    middleware = HarnessContextCompactionMiddleware.__new__(HarnessContextCompactionMiddleware)
    middleware._budget = build_budget(128000)
    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=HarnessContext(user_id="u", username="u", thread_id="t", hitl_status=hitl)
        )
    )
    return middleware, request, messages, _ThreadState(calls=10)


def test_policy_levels() -> None:
    middleware, request, messages, state = _policy()
    assert not middleware._decide(request=request, messages=messages, total=50000, state=state).compact

    middleware, request, messages, state = _policy()
    decision = middleware._decide(request=request, messages=messages, total=80000, state=state)
    assert decision.compact and decision.reason == "soft_watermark"

    middleware, request, messages, state = _policy()
    decision = middleware._decide(request=request, messages=messages, total=100000, state=state)
    assert decision.compact and decision.forced


def test_policy_defers_when_busy() -> None:
    middleware, request, messages, state = _policy(busy=True)
    decision = middleware._decide(request=request, messages=messages, total=80000, state=state)
    assert not decision.compact and decision.deferred

    state.defer_count = 3
    decision = middleware._decide(request=request, messages=messages, total=80000, state=state)
    assert decision.compact and decision.forced


def test_policy_blocks_hitl_and_cooldown() -> None:
    middleware, request, messages, state = _policy(hitl="pending")
    assert not middleware._decide(request=request, messages=messages, total=80000, state=state).compact

    middleware, request, messages, state = _policy()
    state.last_compact_call = 9
    assert not middleware._decide(request=request, messages=messages, total=80000, state=state).compact


def test_autotune_recommend() -> None:
    by_id, reason = recommend(
        {"trigger": 0.70, "hard": 0.88, "keep": 0.25},
        {"samples": 20, "early": 10, "forced": 12, "avg_fidelity": 0.82},
    )
    assert by_id, reason
    assert by_id["keep"] > 0.25
    assert by_id["trigger"] <= 0.70
    assert by_id["keep"] < by_id["trigger"] < by_id["hard"] <= 1


def test_integrity_ignores_mutable_evaluation() -> None:
    event = {
        "compaction_id": "cp-1",
        "trigger": {"type": "soft", "used_tokens": 80000},
        "scope": {"messages_before": 8, "messages_after": 2},
        "compaction_result": {"before_tokens": 80000, "after_tokens": 30000},
        "start": "2026-11-05T00:00:00Z",
        "end": "2026-11-05T00:00:05Z",
        "evaluation": {"timing": "good"},
    }
    first = _event_integrity(event)
    event["evaluation"] = {"timing": "early"}
    second = _event_integrity(event)
    assert first["hash"] == second["hash"]
    assert first["algo"] == "sha256"


def main() -> int:
    tests = [
        test_budget_numbers,
        test_budget_small_window,
        test_anchor_extraction_and_fidelity,
        test_evaluator_timing,
        test_policy_levels,
        test_policy_defers_when_busy,
        test_policy_blocks_hitl_and_cooldown,
        test_autotune_recommend,
        test_integrity_ignores_mutable_evaluation,
    ]
    for test in tests:
        test()
        print(f"{test.__name__}: ok", flush=True)
    print("all unit checks passed", flush=True)
    # 该环境的部分依赖注册了非守护线程，正常退出会卡住；测试脚本强制退出。
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
