"""消息级反馈接口。

点击只写一条账本记录，不调用模型、不写 checkpoint、不进入上下文。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.config import FEEDBACK_DAILY_LIMIT, FEEDBACK_ENABLED
from agent.feedback_store import (
    bump_daily_counter,
    upsert_feedback,
)
from agent.schema import FeedbackRequest, FeedbackResponse
from api.agent_loader import agent_loader


router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """写入或覆盖一条消息反馈；会话已归档时拒绝。"""
    if not FEEDBACK_ENABLED:
        raise HTTPException(status_code=403, detail="消息反馈功能已关闭")
    from api.identity import effective_user_id

    request.user_id = effective_user_id(request.user_id)
    # 归属校验同时拒绝不存在与已归档的会话。
    await agent_loader.require_session(request.user_id, request.thread_id)
    store = await agent_loader.get_store()
    allowed = await bump_daily_counter(
        store,
        user_id=request.user_id,
        limit=FEEDBACK_DAILY_LIMIT,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="今日反馈次数已达上限，请明天再试",
        )
    record = await upsert_feedback(
        store,
        user_id=request.user_id,
        thread_id=request.thread_id,
        message_id=request.message_id,
        action=request.action,
        user_excerpt=request.user_excerpt,
        answer_excerpt=request.answer_excerpt,
        meta=request.meta,
    )
    if record is None:
        raise HTTPException(status_code=500, detail="反馈写入失败，请稍后重试")
    return FeedbackResponse(
        message_id=request.message_id,
        action=str(record.get("action") or request.action),
    )
