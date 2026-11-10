"""① 结构解析器：UI JSON → TaskFlow schema。

输入是 JSON 而非源码，只做结构映射与默认值填充，不判断语义。
pydantic 校验错误在此转换为 FlowValidationError，由 API 层返回 422。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .models import FlowIssue, FlowValidationError, TaskFlow, new_flow_id


def parse(ui_payload: dict[str, Any]) -> TaskFlow:
    """把 UI 提交的字典解析为 TaskFlow；结构不可读时抛 FlowValidationError。"""
    if not isinstance(ui_payload, dict):
        raise FlowValidationError(
            [FlowIssue(pass_name="parser", message="任务流必须是 JSON 对象")]
        )
    payload = dict(ui_payload)
    payload.setdefault("id", new_flow_id())
    try:
        return TaskFlow.model_validate(payload)
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            # 尝试提取 step_id：loc 形如 steps.2.id
            step_id = None
            parts = list(err.get("loc", ()))
            if "steps" in parts:
                idx = parts.index("steps")
                if idx + 1 < len(parts) and isinstance(parts[idx + 1], int):
                    steps = payload.get("steps") or []
                    pos = parts[idx + 1]
                    if 0 <= pos < len(steps) and isinstance(steps[pos], dict):
                        step_id = steps[pos].get("id")
            issues.append(
                FlowIssue(pass_name="parser", step_id=step_id, message=f"{loc}: {err.get('msg')}")
            )
        raise FlowValidationError(issues) from exc
