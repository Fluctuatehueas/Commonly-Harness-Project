"""消息级反馈账本。

反馈是用户侧信号，与执行轨迹、Token 用量分开存储：

    ("feedback", <user_id>) -> key: <message_id>

同一消息只保留一条最新状态，多次点击按覆盖处理；重试事件使用
``retry:<old_message_id>:<new_message_id>`` 作为 key，避免覆盖原反馈。

账本只保存短摘要，不搬运完整对话，默认不进入上下文，也不在点击时
触发任何模型调用。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.config import (
    FEEDBACK_MAX_ANSWER_EXCERPT,
    FEEDBACK_MAX_RECORDS,
    FEEDBACK_MAX_USER_EXCERPT,
    FEEDBACK_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

FEEDBACK_NAMESPACE_PREFIX = ("feedback",)
ACTION_LIKE = "like"
ACTION_DISLIKE = "dislike"
ACTION_NONE = "none"
ACTION_RETRY = "retry"
VALID_ACTIONS = {ACTION_LIKE, ACTION_DISLIKE, ACTION_NONE}
MAX_HISTORY = 5
RETRY_KEY_PREFIX = "retry:"
DAILY_COUNTER_PREFIX = "__daily__:"
MAX_LIST_LIMIT = 20_000


def feedback_namespace(user_id: str) -> tuple[str, ...]:
    """返回某个用户的反馈命名空间。"""
    return (*FEEDBACK_NAMESPACE_PREFIX, user_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_date() -> str:
    """限流按本地日期统计，与用户感知一致。"""
    return datetime.now().date().isoformat()


def truncate_text(value: Any, limit: int) -> str:
    """把任意值转成有长度上限的文本。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def _is_internal_key(key: str) -> bool:
    """内部计数键不参与反馈列表；retry: 前缀是正常事件。"""
    return key.startswith("__")


def _record(item: Any) -> dict[str, Any] | None:
    value = getattr(item, "value", None)
    return dict(value) if isinstance(value, dict) else None


async def upsert_feedback(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    message_id: str,
    action: str,
    user_excerpt: str = "",
    answer_excerpt: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """写入或覆盖一条反馈；action 为 none 时保留记录但标记取消。"""
    if store is None or not user_id or not thread_id or not message_id:
        return None
    if action not in VALID_ACTIONS:
        return None

    namespace = feedback_namespace(user_id)
    existing = _record(await store.aget(namespace, message_id)) or {}
    previous_action = str(existing.get("action") or "")
    now = _now()
    history = [
        item
        for item in existing.get("history", [])
        if isinstance(item, dict) and item.get("action")
    ]
    # 同值重复点击不追加历史，避免连点把历史挤满。
    if previous_action and previous_action != action:
        history.append({"action": action, "at": now})
        history = history[-MAX_HISTORY:]

    record = {
        "message_id": message_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "action": action,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "user_excerpt": truncate_text(
            user_excerpt, FEEDBACK_MAX_USER_EXCERPT
        ),
        "answer_excerpt": truncate_text(
            answer_excerpt, FEEDBACK_MAX_ANSWER_EXCERPT
        ),
        "meta": {
            **(existing.get("meta") if isinstance(existing.get("meta"), dict) else {}),
            **(meta or {}),
        },
        "history": history,
    }
    try:
        await store.aput(namespace, message_id, record, index=False)
    except Exception:
        logger.warning(
            "写入消息反馈失败：user=%s thread=%s message=%s",
            user_id,
            thread_id,
            message_id,
            exc_info=True,
        )
        return None
    return record


async def save_retry_event(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    old_message_id: str,
    new_message_id: str,
    old_excerpt: str = "",
) -> dict[str, Any] | None:
    """记录一次覆盖式重试，供后续对比统计。"""
    if store is None or not user_id or not thread_id or not old_message_id:
        return None
    key = f"{RETRY_KEY_PREFIX}{old_message_id}:{new_message_id}"
    record = {
        "message_id": old_message_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "action": ACTION_RETRY,
        "created_at": _now(),
        "updated_at": _now(),
        "user_excerpt": "",
        "answer_excerpt": truncate_text(
            old_excerpt, FEEDBACK_MAX_ANSWER_EXCERPT
        ),
        "meta": {
            "old_message_id": old_message_id,
            "new_message_id": new_message_id,
        },
        "history": [],
    }
    try:
        await store.aput(feedback_namespace(user_id), key, record, index=False)
    except Exception:
        logger.warning(
            "写入重试反馈失败：user=%s thread=%s", user_id, thread_id, exc_info=True
        )
        return None
    return record


async def bump_daily_counter(
    store: Any,
    *,
    user_id: str,
    limit: int,
) -> bool:
    """按本地日期累加写入次数；超过上限返回 False。"""
    if store is None or not user_id:
        return False
    namespace = feedback_namespace(user_id)
    key = f"{DAILY_COUNTER_PREFIX}{_local_date()}"
    try:
        existing = _record(await store.aget(namespace, key)) or {}
        count = int(existing.get("count") or 0)
        if limit > 0 and count >= limit:
            return False
        await store.aput(
            namespace,
            key,
            {"count": count + 1, "updated_at": _now()},
            index=False,
        )
        return True
    except Exception:
        logger.warning("反馈限流计数失败：user=%s", user_id, exc_info=True)
        # 计数失败时不阻塞正常反馈，避免把限流做成故障点。
        return True


async def list_feedback(
    store: Any,
    *,
    user_id: str,
    thread_id: str | None = None,
    actions: set[str] | None = None,
    limit: int = MAX_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """读取用户反馈；可按会话与动作过滤。"""
    if store is None or not user_id:
        return []
    try:
        items = await store.asearch(feedback_namespace(user_id), limit=limit)
    except Exception:
        logger.warning("读取消息反馈失败：user=%s", user_id, exc_info=True)
        return []
    records: list[dict[str, Any]] = []
    for item in items:
        key = str(getattr(item, "key", ""))
        value = _record(item)
        if value is None or _is_internal_key(key):
            continue
        if thread_id and str(value.get("thread_id") or "") != thread_id:
            continue
        if actions and str(value.get("action") or "") not in actions:
            continue
        records.append(value)
    records.sort(key=lambda item: str(item.get("updated_at", "")))
    return records


async def feedback_map_for_thread(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
) -> dict[str, str]:
    """返回某会话内 message_id -> like/dislike 的映射，供历史回显。"""
    records = await list_feedback(
        store,
        user_id=user_id,
        thread_id=thread_id,
        actions={ACTION_LIKE, ACTION_DISLIKE},
    )
    return {
        str(record.get("message_id")): str(record.get("action"))
        for record in records
        if record.get("message_id")
    }


async def delete_feedback_thread(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
) -> None:
    """删除某会话的全部反馈，与会话同生命周期。"""
    if store is None or not user_id or not thread_id:
        return
    namespace = feedback_namespace(user_id)
    try:
        items = await store.asearch(namespace, limit=MAX_LIST_LIMIT)
    except Exception:
        logger.warning("清理会话反馈失败：user=%s", user_id, exc_info=True)
        return
    for item in items:
        value = _record(item)
        if value is None:
            continue
        if str(value.get("thread_id") or "") == thread_id:
            try:
                await store.adelete(namespace, str(getattr(item, "key", "")))
            except Exception:
                logger.warning("删除反馈记录失败：user=%s", user_id, exc_info=True)


async def delete_feedback_user(store: Any, *, user_id: str) -> None:
    """删除某用户的全部反馈数据。"""
    if store is None or not user_id:
        return
    namespace = feedback_namespace(user_id)
    try:
        items = await store.asearch(namespace, limit=MAX_LIST_LIMIT)
    except Exception:
        logger.warning("清空用户反馈失败：user=%s", user_id, exc_info=True)
        return
    for item in items:
        try:
            await store.adelete(namespace, str(getattr(item, "key", "")))
        except Exception:
            logger.warning("删除反馈记录失败：user=%s", user_id, exc_info=True)


async def prune_feedback(store: Any, *, user_id: str) -> None:
    """按保留期与条数上限清理账本，避免无限增长。"""
    if store is None or not user_id:
        return
    namespace = feedback_namespace(user_id)
    try:
        items = await store.asearch(namespace, limit=MAX_LIST_LIMIT)
    except Exception:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=FEEDBACK_RETENTION_DAYS)
    kept: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        key = str(getattr(item, "key", ""))
        value = _record(item)
        if value is None:
            continue
        if _is_internal_key(key):
            # 内部计数只保留最近 7 天，避免日期键永久堆积。
            if key.startswith(DAILY_COUNTER_PREFIX):
                date_text = key.removeprefix(DAILY_COUNTER_PREFIX)
                try:
                    if datetime.fromisoformat(date_text).date() < (
                        datetime.now().date() - timedelta(days=7)
                    ):
                        await store.adelete(namespace, key)
                except ValueError:
                    pass
            continue
        try:
            updated = datetime.fromisoformat(str(value.get("updated_at", "")))
        except ValueError:
            updated = datetime.now(timezone.utc)
        if updated < cutoff:
            await store.adelete(namespace, key)
            continue
        kept.append((key, value))
    if len(kept) <= FEEDBACK_MAX_RECORDS:
        return
    kept.sort(key=lambda pair: str(pair[1].get("updated_at", "")))
    for key, _ in kept[: len(kept) - FEEDBACK_MAX_RECORDS]:
        await store.adelete(namespace, key)
