"""会话产出文件账本。

每轮回答产出的文件清单按「助手消息 ID」持久化，与聊天记录同生命周期：

    ("session_artifacts", <user_id>, <thread_id>) -> key: <message_id>

前端每轮结束把本轮新增文件写进来，刷新或重新打开会话时再按消息 ID 取回，
从而让产出卡片像一条消息一样固定在自己那一轮，而不是随最新对话漂移。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ARTIFACT_NAMESPACE_PREFIX = ("session_artifacts",)
MAX_FILES_PER_MESSAGE = 300
MAX_FETCH = 500


def _namespace(user_id: str, thread_id: str) -> tuple[str, ...]:
    return (*ARTIFACT_NAMESPACE_PREFIX, user_id, thread_id)


def _normalize_files(raw: Any) -> list[dict[str, Any]]:
    """只保留前端展示需要的字段，避免把任意 payload 写进 Store。"""
    files: list[dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_FILES_PER_MESSAGE]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        origin = "project" if item.get("origin") == "project" else "sandbox"
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        files.append(
            {
                "path": path,
                "size": size,
                "modified_at": item.get("modified_at") or None,
                "origin": origin,
            }
        )
    return files


async def save_message_artifacts(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    message_id: str,
    files: Any,
) -> list[dict[str, Any]]:
    """写入（覆盖）某条助手消息的产出文件清单；空清单也保留，表示“本轮无产出”。"""
    if store is None or not user_id or not thread_id or not message_id:
        return []
    normalized = _normalize_files(files)
    record = {
        "message_id": message_id,
        "files": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await store.aput(
            _namespace(user_id, thread_id), message_id, record, index=False
        )
    except Exception:
        logger.warning(
            "写入会话产出文件失败：user=%s thread=%s message=%s",
            user_id,
            thread_id,
            message_id,
            exc_info=True,
        )
    return normalized


async def artifacts_for_thread(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """返回该会话内 ``message_id -> files`` 的映射，供历史回显。"""
    if store is None or not user_id or not thread_id:
        return {}
    try:
        items = await store.asearch(_namespace(user_id, thread_id), limit=MAX_FETCH)
    except Exception:
        logger.warning("读取会话产出文件失败：user=%s thread=%s", user_id, thread_id, exc_info=True)
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            continue
        message_id = str(value.get("message_id") or getattr(item, "key", "") or "")
        if not message_id:
            continue
        result[message_id] = _normalize_files(value.get("files"))
    return result


async def delete_thread_artifacts(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
) -> None:
    """删除某会话的全部产出文件记录，与会话同生命周期。"""
    if store is None or not user_id or not thread_id:
        return
    namespace = _namespace(user_id, thread_id)
    try:
        items = await store.asearch(namespace, limit=MAX_FETCH)
    except Exception:
        logger.warning("清理会话产出文件失败：user=%s thread=%s", user_id, thread_id, exc_info=True)
        return
    for item in items:
        try:
            await store.adelete(namespace, str(getattr(item, "key", "")))
        except Exception:
            logger.warning("删除会话产出文件记录失败：user=%s", user_id, exc_info=True)
