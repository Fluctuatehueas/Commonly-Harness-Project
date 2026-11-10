"""消息反馈账本与重试定位的单元自检（无需 pytest）。

运行：python test/test_feedback.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import HTTPException  # noqa: E402

import api.chat as chat_api  # noqa: E402
from agent.feedback_store import (  # noqa: E402
    bump_daily_counter,
    delete_feedback_thread,
    feedback_map_for_thread,
    list_feedback,
    save_retry_event,
    upsert_feedback,
)


class FakeItem:
    def __init__(self, key: str, value: dict) -> None:
        self.key = key
        self.value = value


class FakeStore:
    """最小 Store 替身：只实现反馈账本用到的方法。"""

    def __init__(self) -> None:
        self.data: dict[tuple[str, ...], dict[str, dict]] = {}

    async def aget(self, namespace, key):
        value = self.data.get(tuple(namespace), {}).get(key)
        return FakeItem(key, dict(value)) if value is not None else None

    async def aput(self, namespace, key, value, index=False):
        self.data.setdefault(tuple(namespace), {})[key] = dict(value)

    async def asearch(self, namespace, limit=100):
        items = self.data.get(tuple(namespace), {})
        return [FakeItem(key, dict(value)) for key, value in list(items.items())[:limit]]

    async def adelete(self, namespace, key):
        self.data.get(tuple(namespace), {}).pop(key, None)


def message(role: str, content: str, message_id: str, tool_calls=None):
    return SimpleNamespace(
        type=role,
        content=content,
        id=message_id,
        tool_calls=tool_calls or [],
    )


def snapshot(messages, checkpoint_id: str, next_nodes):
    return SimpleNamespace(
        values={"messages": messages},
        config={"configurable": {"checkpoint_id": checkpoint_id}},
        next=next_nodes,
        created_at=checkpoint_id,
    )


class FakeAgent:
    def __init__(self, state, history) -> None:
        self._state = state
        self._history = history
        # 撤回需要用真实节点名作为 aupdate_state 的写入方。
        self.nodes = {"model": object(), "tools": object()}

    async def aget_state(self, _config):
        return self._state

    async def aget_state_history(self, _config):
        for item in self._history:
            yield item


async def install_agent(agent: FakeAgent) -> None:
    async def get_agent(**_kwargs):
        return agent

    chat_api.agent_loader.get_agent_for_user = get_agent
    chat_api.agent_loader.create_config = lambda **kwargs: {
        "configurable": {"thread_id": kwargs.get("thread_id")}
    }
    chat_api.agent_loader.require_session = lambda *_args, **_kwargs: asyncio.sleep(0)


async def test_feedback_idempotent_and_truncate() -> None:
    store = FakeStore()
    first = await upsert_feedback(
        store,
        user_id="u1",
        thread_id="t1",
        message_id="m1",
        action="like",
        user_excerpt="u" * 500,
        answer_excerpt="a" * 900,
    )
    assert first is not None
    assert len(first["user_excerpt"]) <= 201
    assert len(first["answer_excerpt"]) <= 401

    again = await upsert_feedback(
        store, user_id="u1", thread_id="t1", message_id="m1", action="like"
    )
    assert again is not None and again["history"] == []
    assert len(await list_feedback(store, user_id="u1")) == 1

    switched = await upsert_feedback(
        store, user_id="u1", thread_id="t1", message_id="m1", action="dislike"
    )
    assert switched is not None and switched["history"][-1]["action"] == "dislike"
    assert await feedback_map_for_thread(store, user_id="u1", thread_id="t1") == {
        "m1": "dislike"
    }

    await upsert_feedback(
        store, user_id="u1", thread_id="t1", message_id="m1", action="none"
    )
    assert await feedback_map_for_thread(store, user_id="u1", thread_id="t1") == {}


async def test_feedback_thread_filter_and_delete() -> None:
    store = FakeStore()
    await upsert_feedback(
        store, user_id="u1", thread_id="t1", message_id="m1", action="like"
    )
    await upsert_feedback(
        store, user_id="u1", thread_id="t2", message_id="m2", action="dislike"
    )
    assert len(await list_feedback(store, user_id="u1", thread_id="t1")) == 1
    await delete_feedback_thread(store, user_id="u1", thread_id="t1")
    remaining = await list_feedback(store, user_id="u1")
    assert [item["message_id"] for item in remaining] == ["m2"]


async def test_retry_event_is_separate_from_like_map() -> None:
    store = FakeStore()
    await save_retry_event(
        store,
        user_id="u1",
        thread_id="t1",
        old_message_id="a-old",
        new_message_id="a-new",
        old_excerpt="old answer",
    )
    records = await list_feedback(store, user_id="u1", actions={"retry"})
    assert len(records) == 1
    assert records[0]["meta"]["new_message_id"] == "a-new"
    assert await feedback_map_for_thread(store, user_id="u1", thread_id="t1") == {}


async def test_daily_limit() -> None:
    store = FakeStore()
    assert await bump_daily_counter(store, user_id="u1", limit=1)
    assert not await bump_daily_counter(store, user_id="u1", limit=1)


async def test_prepare_retry_uses_checkpoint_before_answer() -> None:
    human = message("human", "hello", "h1")
    answer = message("ai", "answer", "a1")
    agent = FakeAgent(
        SimpleNamespace(values={"messages": [human, answer]}, interrupts=()),
        [
            snapshot([human, answer], "c2", ()),
            snapshot([human], "c1", ("model",)),
        ],
    )
    await install_agent(agent)
    plan = await chat_api._prepare_retry(
        user_id="u1",
        username="u1",
        thread_id="t1",
        requested_message_id="a1",
    )
    assert plan.checkpoint_id == "c1"
    assert plan.old_message_id == "a1"


async def test_prepare_retry_rejects_high_risk_tool() -> None:
    human = message("human", "write it", "h1")
    tool_call = message(
        "ai", "", "a0", tool_calls=[{"name": "write_file", "args": {}}]
    )
    answer = message("ai", "done", "a1")
    agent = FakeAgent(
        SimpleNamespace(values={"messages": [human, tool_call, answer]}, interrupts=()),
        [snapshot([human], "c1", ("model",))],
    )
    await install_agent(agent)
    try:
        await chat_api._prepare_retry(user_id="u1", username="u1", thread_id="t1")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "写操作" in str(exc.detail)
    else:
        raise AssertionError("副作用工具轮次应被拒绝")


async def test_prepare_retry_rejects_interrupt_and_history_answer() -> None:
    human = message("human", "hello", "h1")
    answer = message("ai", "answer", "a1")

    interrupted = FakeAgent(
        SimpleNamespace(
            values={"messages": [human, answer]}, interrupts=(object(),)
        ),
        [snapshot([human], "c1", ("model",))],
    )
    await install_agent(interrupted)
    try:
        await chat_api._prepare_retry(user_id="u1", username="u1", thread_id="t1")
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("等待审批的会话不应允许重试")

    not_final = FakeAgent(
        SimpleNamespace(
            values={
                "messages": [
                    human,
                    answer,
                    message("human", "next", "h2"),
                    message("ai", "", "a2", tool_calls=[{"name": "read_file"}]),
                ]
            },
            interrupts=(),
        ),
        [],
    )
    await install_agent(not_final)
    try:
        await chat_api._prepare_retry(user_id="u1", username="u1", thread_id="t1")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "最终回答" in str(exc.detail)
    else:
        raise AssertionError("最后一条不是最终回答时应被拒绝")


async def test_prepare_withdraw_targets_previous_turn() -> None:
    h1 = message("human", "u1", "h1")
    a1 = message("ai", "a1", "a1")
    h2 = message("human", "u2", "h2")
    a2 = message("ai", "a2", "a2")
    agent = FakeAgent(
        SimpleNamespace(values={"messages": [h1, a1, h2, a2]}, interrupts=()),
        [
            snapshot([h1, a1, h2, a2], "c4", ()),
            snapshot([h1, a1, h2], "c3", ("model",)),
            snapshot([h1, a1], "c2", ()),
        ],
    )
    await install_agent(agent)
    _, plan = await chat_api._prepare_withdraw(
        user_id="u1", username="u1", thread_id="t1"
    )
    assert plan.reset_thread is False
    assert plan.message_id == "h2"
    assert plan.content == "u2"
    assert plan.checkpoint_config["configurable"]["checkpoint_id"] == "c2"
    # 轨迹边界取「最早包含该用户消息」的检查点时间。
    assert plan.cutoff == "c3"


async def test_prepare_withdraw_first_or_pending() -> None:
    h1 = message("human", "only", "h1")
    a1 = message("ai", "a1", "a1")
    first = FakeAgent(
        SimpleNamespace(values={"messages": [h1, a1]}, interrupts=()),
        [],
    )
    await install_agent(first)
    _, plan = await chat_api._prepare_withdraw(
        user_id="u1", username="u1", thread_id="t1"
    )
    assert plan.reset_thread is True
    assert plan.content == "only"

    pending = FakeAgent(
        SimpleNamespace(
            values={
                "messages": [
                    h1,
                    a1,
                    message("human", "u2", "h2"),
                    message("ai", "a2", "a2"),
                ]
            },
            interrupts=(),
        ),
        [
            snapshot([h1, a1, message("human", "u2", "h2"), message("ai", "a2", "a2")], "c4", ()),
            snapshot([h1, a1], "c2", ("tools",)),
        ],
    )
    await install_agent(pending)
    try:
        await chat_api._prepare_withdraw(
            user_id="u1", username="u1", thread_id="t1"
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "未完成任务" in str(exc.detail)
    else:
        raise AssertionError("上一轮有未完成任务时应拒绝撤回")


async def test_prepare_withdraw_skips_pending_start_checkpoint() -> None:
    """上一条用户消息没有被回答时，应跳过 __start__ 待执行态，选更早的稳定态。"""
    h1 = message("human", "u1", "h1")
    h2 = message("human", "u2", "h2")
    h3 = message("human", "u3", "h3")
    ai = message("ai", "a3", "a3")
    agent = FakeAgent(
        SimpleNamespace(values={"messages": [h1, h2, h3, ai]}, interrupts=()),
        [
            snapshot([h1, h2, h3, ai], "c5", ()),
            snapshot([h1, h2, h3], "c4", ("model",)),
            snapshot([h1, h2], "c3", ("__start__",)),
            snapshot([h1, h2], "c2", ()),
            snapshot([h1], "c1", ()),
        ],
    )
    await install_agent(agent)
    _, plan = await chat_api._prepare_withdraw(
        user_id="u1", username="u1", thread_id="t1"
    )
    assert plan.reset_thread is False
    assert plan.message_id == "h3"
    assert plan.checkpoint_config["configurable"]["checkpoint_id"] == "c2"


async def main_async() -> None:
    tests = [
        test_feedback_idempotent_and_truncate,
        test_feedback_thread_filter_and_delete,
        test_retry_event_is_separate_from_like_map,
        test_daily_limit,
        test_prepare_retry_uses_checkpoint_before_answer,
        test_prepare_retry_rejects_high_risk_tool,
        test_prepare_retry_rejects_interrupt_and_history_answer,
        test_prepare_withdraw_targets_previous_turn,
        test_prepare_withdraw_first_or_pending,
        test_prepare_withdraw_skips_pending_start_checkpoint,
    ]
    for test in tests:
        await test()
        print(f"{test.__name__}: ok", flush=True)
    print("all feedback checks passed", flush=True)


def main() -> int:
    asyncio.run(main_async())
    # 部分依赖注册了非守护线程，正常退出可能卡住；测试脚本强制退出。
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
