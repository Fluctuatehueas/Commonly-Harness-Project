"""证据清单提取。

判官只根据本轮真实发生过的工具调用、工具结果、计划项与委派结论判断，
不读历史轮次，也不读主 Agent 的思考过程。这里负责把这些材料裁剪成
固定预算内的证据列表。
"""

from __future__ import annotations

import json
from typing import Any

EVIDENCE_LIMIT = 20
EVIDENCE_MAX_CHARS = 300
# 会写盘或执行命令的工具；用于风险分级。
HIGH_RISK_TOOLS = {
    "write_file",
    "edit_file",
    "host_write_file",
    "host_execute",
    "publish_to_project",
    "run_in_sandbox",
    "compile_project",
}
# 委派工具：结果需要单独标记为子 Agent 结论。
DELEGATION_TOOLS = {"task", "start_async_task"}


def message_text(message: Any) -> str:
    """把消息内容归一化为文本，兼容多模态块列表。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _truncate(value: Any, limit: int = EVIDENCE_MAX_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _args_text(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(args)


def _current_run_messages(messages: list[Any]) -> list[Any]:
    """只取最后一条用户消息之后的材料，避免历史轮次污染证据。"""
    start = 0
    for index, message in enumerate(messages):
        if getattr(message, "type", None) in {"human", "user"}:
            start = index
    return messages[start:]


def collect_evidence(
    messages: list[Any],
    *,
    limit: int = EVIDENCE_LIMIT,
    max_chars: int = EVIDENCE_MAX_CHARS,
) -> list[dict[str, Any]]:
    """按时间顺序收集本轮证据，超出上限时保留最近的条目。"""
    items: list[dict[str, Any]] = []
    for message in _current_run_messages(messages):
        message_type = getattr(message, "type", None)
        if message_type == "ai":
            for call in list(getattr(message, "tool_calls", []) or []):
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or call.get("tool_call_id") or "")
                if not name:
                    continue
                items.append(
                    {
                        "ref": f"tool:{name}#{call_id}",
                        "kind": "tool_call",
                        "name": name,
                        "status": "called",
                        "summary": _truncate(_args_text(call.get("args")), max_chars),
                    }
                )
            continue
        if message_type != "tool":
            continue

        name = str(getattr(message, "name", "") or "")
        call_id = str(getattr(message, "tool_call_id", "") or "")
        status = str(getattr(message, "status", "") or "done")
        text = _truncate(message_text(message), max_chars)
        if name in DELEGATION_TOOLS:
            items.append(
                {
                    "ref": f"subagent:{name}#{call_id}",
                    "kind": "subagent",
                    "name": name,
                    "status": status,
                    "summary": text,
                }
            )
        else:
            items.append(
                {
                    "ref": f"tool_result:{name}#{call_id}",
                    "kind": "tool_result",
                    "name": name,
                    "status": status,
                    "summary": text,
                }
            )

    # 只保留最近 N 条：早期证据通常已被后续结论覆盖。
    return items[-limit:]


def plan_items_from_text(plan: str) -> list[dict[str, Any]]:
    """从 create_work_plan 的 JSON 计划里提取工作项。"""
    if not plan:
        return []
    start = plan.find("{")
    end = plan.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        payload = json.loads(plan[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    items: list[dict[str, Any]] = []
    for wave in payload.get("waves") or []:
        if not isinstance(wave, dict):
            continue
        for item in wave.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            item_id = str(item.get("id") or "")
            if title or item_id:
                items.append({"id": item_id, "title": title})
    return items


def _tool_names(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for message in _current_run_messages(messages):
        for call in list(getattr(message, "tool_calls", []) or []):
            if isinstance(call, dict) and call.get("name"):
                names.append(str(call["name"]))
    return names


def risk_tier(messages: list[Any], answer: str) -> str:
    """粗粒度风险分级：t2 高风险工具 / t1 有工具或数字代码 / t0 闲聊。"""
    return risk_tier_from_names(_tool_names(messages), answer)


def risk_tier_from_names(names: list[str], answer: str) -> str:
    """按工具名与回答内容分级；供只有证据清单、没有原始消息的调用方使用。"""
    if any(name in HIGH_RISK_TOOLS for name in names):
        return "t2"
    if names:
        return "t1"
    if any(char.isdigit() for char in answer) or "```" in answer:
        return "t1"
    return "t0"


def format_evidence_for_prompt(evidence: list[dict[str, Any]]) -> str:
    """把证据清单转成提示词里的紧凑列表。"""
    if not evidence:
        return "（本轮没有可引用的工具证据）"
    lines: list[str] = []
    for item in evidence:
        lines.append(
            f"- {item.get('ref')} | kind={item.get('kind')} | "
            f"status={item.get('status')} | {item.get('summary')}"
        )
    return "\n".join(lines)


__all__ = [
    "DELEGATION_TOOLS",
    "EVIDENCE_LIMIT",
    "EVIDENCE_MAX_CHARS",
    "HIGH_RISK_TOOLS",
    "collect_evidence",
    "format_evidence_for_prompt",
    "message_text",
    "plan_items_from_text",
    "risk_tier",
    "risk_tier_from_names",
]
