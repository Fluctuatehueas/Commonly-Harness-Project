"""需要人工补充信息或做选择时使用的 Human-in-the-Loop 工具。

统一产出 ``HitlRequest``；接入方渲染表单/选项后通过中断恢复载荷
回传 ``HitlResponse``。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from langgraph.config import get_config
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.hitl import (
    HitlField,
    HitlOption,
    build_choice_request,
    build_information_request,
    interrupt_payload,
    parse_hitl_response,
    response_text,
    stable_hitl_id,
)


logger = logging.getLogger(__name__)

# key -> 当前活动 HITL ID；用于阻止同一次运行里并行产生多个人工介入。
_ACTIVE_HITL: dict[str, str] = {}


def _parse_fields(raw: str) -> list[HitlField]:
    """解析可选的 fields_json；非法输入静默退化为单字段表单。"""
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    fields: list[HitlField] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            fields.append(HitlField(**item))
        except (TypeError, ValueError):
            continue
    return fields


def _thread_id() -> str:
    try:
        config = get_config() or {}
    except Exception:
        return ""
    return str((config.get("configurable") or {}).get("thread_id") or "")


def _resume(request: Any, *, name: str, instruction: str) -> str:
    """统一处理一次人工介入，并保证同一会话同一时刻只有一个活动请求。"""
    thread_id = _thread_id() or "default"
    key = thread_id
    stable_id = stable_hitl_id(thread_id, name, instruction)
    request.hitl_id = stable_id

    active = _ACTIVE_HITL.get(key)
    if active and active != stable_id:
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "HITL_IN_PROGRESS",
                    "message": "当前会话已有待处理的人工介入",
                    "hint": "请先完成现有的 HITL 请求，再继续后续工具调用。",
                },
            },
            ensure_ascii=False,
        )

    _ACTIVE_HITL[key] = stable_id
    response = parse_hitl_response(interrupt(interrupt_payload(request)))
    _ACTIVE_HITL.pop(key, None)

    if response.hitl_id and response.hitl_id != request.hitl_id:
        logger.warning(
            "HITL hitl_id 不匹配：expected=%s got=%s，按当前中断继续",
            request.hitl_id,
            response.hitl_id,
        )
    return response_text(response)


@tool
def request_additional_info(
    information_needed: str,
    context: str = "",
    fields_json: str = "",
) -> str:
    """暂停当前任务，要求用户补充继续执行所需的信息。

    Args:
        information_needed: 需要用户补充的内容及原因。
        context: 可选的已知上下文，帮助用户判断如何补充。
        fields_json: 可选的多字段表单定义（JSON 数组），
            元素形如 {"name":"sceneName","label":"场景名","type":"text","required":true}。
            留空时生成一个必填文本域。

    Returns:
        会话恢复时由前端提交的 JSON 文本。
    """
    request = build_information_request(
        title="需要补充信息",
        description=information_needed,
        fields=_parse_fields(fields_json),
        metadata={"context": context},
    )
    return _resume(
        request,
        name="request_additional_info",
        instruction=f"{information_needed}|{context}|{fields_json}",
    )


class ChoiceOptionInput(BaseModel):
    """推荐选项。"""

    value: str = Field(description="选项值，提交时原样回传")
    label: str = Field(description="展示给用户的文本")
    description: str = Field(default="", description="选项说明")
    recommended: bool = Field(default=False, description="是否为推荐项")


class UserChoiceInput(BaseModel):
    """推荐选项请求。"""

    question: str = Field(description="需要用户决定的问题")
    options: list[ChoiceOptionInput] = Field(description="2-5 个候选方案")
    allow_multiple: bool = Field(default=False, description="是否允许多选")
    allow_free_text: bool = Field(default=True, description="是否允许补充自由文本")
    context: str = Field(default="", description="可选上下文")


@tool(args_schema=UserChoiceInput)
def request_user_choice(
    question: str,
    options: list[ChoiceOptionInput],
    allow_multiple: bool = False,
    allow_free_text: bool = True,
    context: str = "",
) -> str:
    """暂停任务，给用户一组推荐选项供其选择（可附带自由文本）。"""
    request = build_choice_request(
        title="需要你选择",
        description=question,
        options=[
            HitlOption(
                value=item.value,
                label=item.label,
                description=item.description,
                recommended=item.recommended,
            )
            for item in options
        ],
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        metadata={"context": context},
    )
    return _resume(
        request,
        name="request_user_choice",
        instruction=(
            f"{question}|"
            f"{json.dumps([item.model_dump() for item in options], ensure_ascii=False)}|"
            f"{context}"
        ),
    )


__all__ = ["request_additional_info", "request_user_choice"]
