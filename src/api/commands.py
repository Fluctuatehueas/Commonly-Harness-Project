"""命令目录与只读执行接口。

写操作与会话操作由前端执行；本接口只开放 ``executor=backend`` 的查询命令，
Agent 工具也复用同一套处理器。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.identity import current_user_id
from commands import registry
from commands.schema import CommandContext, CommandError
from commands.service import execute

router = APIRouter(prefix="/commands", tags=["commands"])


class ExecuteRequest(BaseModel):
    command: str = Field(..., max_length=64, description="命令名，可带或不带前导 /")
    args: list[str] = Field(default_factory=list, max_length=16)
    flags: dict[str, Any] = Field(default_factory=dict)
    thread_id: str = Field("", max_length=128)
    raw: str = Field("", max_length=2000)


@router.get("/catalog")
async def command_catalog() -> dict[str, Any]:
    """返回当前宿主支持的命令目录（前端缓存 5 分钟）。"""
    return {"version": 1, "commands": registry.catalog(visible_only=True)}


@router.post("/execute")
async def execute_command(body: ExecuteRequest) -> dict[str, Any]:
    """执行只读命令；前端命令会返回 COMMAND_EXECUTOR_MISMATCH。"""
    name = body.command.strip().lstrip("/").lower()
    ctx = CommandContext(
        user_id=current_user_id(),
        name=name,
        thread_id=body.thread_id.strip(),
        args=[str(item) for item in body.args][:16],
        flags={str(key): value for key, value in body.flags.items()},
        raw=body.raw.strip() or f"/{name}",
    )
    try:
        result = await execute(ctx)
    except CommandError as error:
        raise HTTPException(status_code=error.status, detail=error.to_dict()) from error
    return result.to_dict()


__all__ = ["router"]
