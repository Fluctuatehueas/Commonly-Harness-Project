"""任务流管理 HTTP 接口。

- CRUD / 发布 / 复制内置流 / 执行记录查询
- 保存与发布都走 compile_flow 管线（解析→校验→编译→索引）；
  校验失败 422，body 为 FlowIssue[]（含 step_id 供前端就地标注）。
- 已发布流再编辑生成新 draft（新 id 不变、status 回 draft、plan 清空），
  不影响在用执行计划；重新发布才替换。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from task_flow import compile_flow
from task_flow.builtin.loader import load_builtin_flows
from task_flow.models import FlowValidationError, TaskFlow
from task_flow.resolvers import mcp_exists, role_exists, skill_exists
from task_flow.store import BUILTIN_USER_ID, TaskFlowStore
from task_flow.validators import ValidationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/task-flows", tags=["task-flow"])

_store: Optional[TaskFlowStore] = None


async def get_store() -> TaskFlowStore:
    """进程级单例；首次使用建连并建表、加载内置流。"""
    global _store
    if _store is None:
        store = TaskFlowStore()
        await store.setup()
        try:
            await load_builtin_flows(store)
        except Exception:
            logger.exception("内置任务流加载失败（不阻断服务）")
        _store = store
    return _store


def _validation_ctx() -> ValidationContext:
    return ValidationContext(
        skill_exists=skill_exists, mcp_exists=mcp_exists, role_exists=role_exists
    )


def _current_user() -> str:
    """当前请求的用户标识；由 X-User-Id 中间件写入。"""
    from agent.identity import current_user_id

    return current_user_id()


def _unprocessable(exc: FlowValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[issue.model_dump() for issue in exc.issues],
    )


# ============================================================
# 请求模型
# ============================================================


class FlowPayload(BaseModel):
    """创建/编辑请求体：直接是 UI 提交的 schema 结构。"""

    payload: dict[str, Any]


class ExecutionStartPayload(BaseModel):
    """显式发起执行（含中途接管）。"""

    flow_id: str
    entry_step: Optional[str] = None
    user_state: str = ""
    thread_id: str = ""


# ============================================================
# 任务流 CRUD
# ============================================================


@router.get("")
async def list_flows(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user_id: str = Query("", max_length=64),
):
    from api.identity import effective_user_id

    user = effective_user_id(user_id) if user_id else _current_user()
    store = await get_store()
    items, total = await store.list_flows(user, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("", status_code=201)
async def create_flow(body: FlowPayload):
    store = await get_store()
    try:
        flow, plan, search_doc = compile_flow(body.payload, _validation_ctx())
    except FlowValidationError as exc:
        raise _unprocessable(exc) from exc
    # 新建保存为 draft：plan 也存（已通过编译），发布仅是状态翻转。
    await store.upsert_flow(flow, _current_user(), plan, search_doc)
    return {"id": flow.id, "status": flow.status, "schema_hash": plan.schema_hash}


@router.get("/{flow_id}")
async def get_flow(flow_id: str):
    store = await get_store()
    row = await store.get_flow(flow_id)
    if not row or (row["user_id"] not in (_current_user(), BUILTIN_USER_ID)):
        raise HTTPException(status_code=404, detail="任务流不存在")
    return row


@router.put("/{flow_id}")
async def update_flow(flow_id: str, body: FlowPayload):
    store = await get_store()
    row = await store.get_flow(flow_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务流不存在")
    if row["user_id"] == BUILTIN_USER_ID:
        raise HTTPException(status_code=403, detail="内置流只读，请使用复制")
    if row["user_id"] != _current_user():
        raise HTTPException(status_code=403, detail="无权编辑他人任务流")

    payload = dict(body.payload)
    payload["id"] = flow_id
    payload["source"] = "user"
    # 已发布流再编辑：回退 draft、plan 由重新编译产出但状态不发布，
    # 路由器只选 published，所以在用 plan 不受影响（发布时才覆盖）。
    try:
        flow, plan, search_doc = compile_flow(payload, _validation_ctx())
    except FlowValidationError as exc:
        raise _unprocessable(exc) from exc
    await store.upsert_flow(flow, _current_user(), plan, search_doc)
    return {"id": flow.id, "status": flow.status, "schema_hash": plan.schema_hash}


@router.delete("/{flow_id}", status_code=204)
async def delete_flow(flow_id: str):
    store = await get_store()
    row = await store.get_flow(flow_id)
    if row and row["user_id"] == BUILTIN_USER_ID:
        raise HTTPException(status_code=403, detail="内置流不可删除")
    deleted = await store.delete_flow(flow_id, _current_user())
    if not deleted:
        raise HTTPException(status_code=404, detail="任务流不存在")


@router.post("/{flow_id}/publish")
async def publish_flow(flow_id: str):
    """发布：以库中 schema 重新跑编译管线（防止绕过保存直接改库），绑定 schema_hash。"""
    store = await get_store()
    row = await store.get_flow(flow_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务流不存在")
    if row["user_id"] != _current_user():
        raise HTTPException(status_code=403, detail="无权发布他人任务流")
    try:
        flow = TaskFlow.model_validate({**row["schema"], "status": "published"})
        flow, plan, search_doc = compile_flow(flow.model_dump(mode="json"), _validation_ctx())
    except FlowValidationError as exc:
        raise _unprocessable(exc) from exc
    await store.upsert_flow(flow, _current_user(), plan, search_doc)
    return {"id": flow.id, "status": "published", "schema_hash": plan.schema_hash}


@router.post("/{flow_id}/fork", status_code=201)
async def fork_flow(flow_id: str):
    """复制为我的任务流（内置流转可编辑 draft）。"""
    store = await get_store()
    row = await store.get_flow(flow_id)
    if not row or (row["user_id"] not in (_current_user(), BUILTIN_USER_ID)):
        raise HTTPException(status_code=404, detail="任务流不存在")
    payload = dict(row["schema"])
    payload.pop("id", None)  # parser 会生成新 id
    payload["source"] = "user"
    payload["status"] = "draft"
    payload["name"] = f"{row['name']}（副本）"
    try:
        flow, plan, search_doc = compile_flow(payload, _validation_ctx())
    except FlowValidationError as exc:
        raise _unprocessable(exc) from exc
    await store.upsert_flow(flow, _current_user(), plan, search_doc)
    return {"id": flow.id, "status": "draft", "forked_from": flow_id}


# ============================================================
# 执行记录
# ============================================================


@router.get("/{flow_id}/executions")
async def list_executions(
    flow_id: str, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)
):
    store = await get_store()
    items, total = await store.list_executions(flow_id, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    store = await get_store()
    row = await store.get_execution(execution_id)
    if not row:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return row
