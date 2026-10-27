"""轨迹事件的 Store 读写工具。

轨迹与用户偏好共用 LangGraph 的 ``StoreBackend``，但使用独立命名空间：

    ("trajectory", <user_id>, <thread_id>) -> key: "<run_id>:<started_at>:<event_id>"

每个事件单独一行，避免并发工具调用同时改写同一个文档造成事件丢失。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from agent.config import TRAJECTORY_MAX_TEXT


logger = logging.getLogger(__name__)

TRAJECTORY_NAMESPACE_PREFIX = ("trajectory",)
# 单次查询上限，避免异常长的会话把接口拖慢。
DEFAULT_EVENT_LIMIT = 5_000


def trajectory_namespace(user_id: str, thread_id: str) -> tuple[str, str, str]:
    """返回某个会话的轨迹命名空间。"""
    return (*TRAJECTORY_NAMESPACE_PREFIX, user_id, thread_id)


def truncate_text(value: Any, limit: int = TRAJECTORY_MAX_TEXT) -> str:
    """把任意值转成有长度上限的文本，防止单个事件撑大 Store。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[已截断，共 {len(text)} 字符]"


async def append_trajectory_event(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    event: dict[str, Any],
) -> None:
    """追加一个轨迹事件；写入失败只告警，不影响正在执行的 Agent。"""
    if store is None or not user_id or not thread_id:
        return
    event_id = str(event.get("id") or uuid4().hex)
    run_id = str(event.get("run_id", ""))
    started_at = str(event.get("start", ""))
    key = f"{run_id}:{started_at}:{event_id}"
    payload = {**event, "id": event_id}
    try:
        await store.aput(
            trajectory_namespace(user_id, thread_id),
            key,
            payload,
            index=False,
        )
    except Exception:
        logger.warning(
            "写入轨迹事件失败：user=%s thread=%s type=%s",
            user_id,
            thread_id,
            event.get("type"),
            exc_info=True,
        )


async def list_trajectory_events(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    limit: int = DEFAULT_EVENT_LIMIT,
) -> list[dict[str, Any]]:
    """读取一个会话的全部轨迹事件，按开始时间升序返回。"""
    if store is None or not user_id or not thread_id:
        return []
    items = await store.asearch(
        trajectory_namespace(user_id, thread_id),
        limit=limit,
    )
    events = [dict(item.value) for item in items]
    events.sort(key=lambda event: str(event.get("start", "")))
    return events


async def delete_trajectory(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
) -> None:
    """删除某会话的全部轨迹事件，避免删除会话后残留数据。"""
    if store is None or not user_id or not thread_id:
        return
    namespace = trajectory_namespace(user_id, thread_id)
    items = await store.asearch(namespace, limit=DEFAULT_EVENT_LIMIT)
    for item in items:
        await store.adelete(namespace, item.key)
