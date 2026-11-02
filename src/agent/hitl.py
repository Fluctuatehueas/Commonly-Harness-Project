"""HITL（人工介入）的统一数据模型。

三种形态：
- information：用户填写表单/自由文本；
- choice：用户从推荐选项中选择（可多选、可补充文本）；
- approval：工具执行审批，由接入方自行实现。

Python 侧统一产出 ``HitlRequest``；接入方通过中断恢复载荷回传
``HitlResponse`` 结构。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class HitlOption(BaseModel):
    """一个推荐选项。"""

    value: str
    label: str
    description: str = ""
    recommended: bool = False


class HitlField(BaseModel):
    """表单字段。"""

    name: str
    label: str
    type: Literal[
        "text", "textarea", "number", "boolean", "select", "multiselect"
    ] = "text"
    required: bool = True
    default: Any = None
    placeholder: str = ""
    description: str = ""
    options: list[HitlOption] = Field(default_factory=list)


class HitlRequest(BaseModel):
    """一次人工介入请求。"""

    hitl_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: Literal["information", "choice", "mixed", "approval", "unknown"] = (
        "information"
    )
    title: str = "需要人工介入"
    description: str = ""
    fields: list[HitlField] = Field(default_factory=list)
    options: list[HitlOption] = Field(default_factory=list)
    allow_multiple: bool = False
    allow_free_text: bool = True
    submit_label: str = "提交"
    cancel_label: str = "取消"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HitlResponse(BaseModel):
    """用户提交的恢复载荷。"""

    hitl_id: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    selected: list[str] = Field(default_factory=list)
    free_text: str = ""
    cancelled: bool = False


RESUME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hitl_id": {"type": "string"},
        "values": {"type": "object"},
        "selected": {"type": "array", "items": {"type": "string"}},
        "free_text": {"type": "string"},
        "cancelled": {"type": "boolean"},
    },
    "required": ["hitl_id"],
}


def new_hitl_id() -> str:
    return uuid4().hex


def stable_hitl_id(*parts: str) -> str:
    """生成跨“中断重放”稳定的 HITL ID。

    LangGraph 恢复时会重新执行中断前的节点代码；如果每次都用随机 ID，
    接入方回传的旧 ID 就会与新建的请求不匹配。用线程 + 工具 + 指令做哈希，
    保证同一次中断在重放后仍是同一个 ID。
    """
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def build_information_request(
    *,
    title: str,
    description: str,
    fields: list[HitlField] | None = None,
    metadata: dict[str, Any] | None = None,
    hitl_id: str = "",
) -> HitlRequest:
    """构造补充信息请求；没有给字段时退化为一个必填文本域。"""
    actual_fields = list(fields or [])
    if not actual_fields:
        actual_fields = [
            HitlField(
                name="information",
                label="补充内容",
                type="textarea",
                required=True,
                description=description[:300],
            )
        ]
    return HitlRequest(
        hitl_id=hitl_id or new_hitl_id(),
        kind="information",
        title=title,
        description=description,
        fields=actual_fields,
        allow_free_text=False,
        metadata=dict(metadata or {}),
    )


def build_choice_request(
    *,
    title: str,
    description: str,
    options: list[HitlOption],
    allow_multiple: bool = False,
    allow_free_text: bool = True,
    metadata: dict[str, Any] | None = None,
    hitl_id: str = "",
) -> HitlRequest:
    """构造推荐选项请求。"""
    return HitlRequest(
        hitl_id=hitl_id or new_hitl_id(),
        kind="mixed" if allow_free_text else "choice",
        title=title,
        description=description,
        options=list(options),
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        metadata=dict(metadata or {}),
    )


def parse_hitl_response(value: Any) -> HitlResponse:
    """把中断恢复载荷归一化为 HitlResponse。"""
    if isinstance(value, str):
        return HitlResponse(free_text=value)
    if not isinstance(value, dict):
        return HitlResponse()
    selected = value.get("selected")
    return HitlResponse(
        hitl_id=str(value.get("hitl_id") or ""),
        values=dict(value.get("values") or {}),
        selected=[str(item) for item in (selected or [])],
        free_text=str(value.get("free_text") or ""),
        cancelled=bool(value.get("cancelled", False)),
    )


def interrupt_payload(request: HitlRequest) -> dict[str, Any]:
    """写入 LangGraph interrupt 的统一载荷。"""
    return {
        "type": "hitl_request",
        "hitl": request.model_dump(exclude_none=True),
        "resume_schema": RESUME_SCHEMA,
    }


def response_text(response: HitlResponse) -> str:
    """把用户提交内容整理成模型可读文本。"""
    payload = {
        "ok": not response.cancelled,
        "cancelled": response.cancelled,
        "values": response.values,
        "selected": response.selected,
        "free_text": response.free_text,
    }
    return json.dumps(payload, ensure_ascii=False)