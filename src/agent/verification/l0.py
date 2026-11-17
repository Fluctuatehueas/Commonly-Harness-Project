"""L0 确定性验证器。

L0 不调用模型，覆盖空产出、工具错误、截断、检索空结果、计划项遗漏、
Schema / 数值断言等可程序化判断的问题。结果统一转成 VerificationIssue。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from agent.verification.types import (
    VerificationIssue,
    VerificationPoint,
    VerificationRequest,
)

DATA_TOOLS = {"search_web", "fetch_web", "knowledge_search"}
READ_ONLY_SUBAGENTS = {
    "reviewer",
    "researcher",
    "web_researcher",
    "verifier_score",
    "verifier_critic",
}
WRITE_CLAIM_TOKENS = ("已写入", "已创建", "已修改", "已删除", "已执行")
CONCLUSION_TOKENS = ("结论", "总结", "建议", "发现", "结果", "PASS", "FAIL")
MIN_TEXT_CHARS = 20

L0Verifier = Callable[[VerificationRequest], list[VerificationIssue]]

_REGISTRY: dict[VerificationPoint, list[L0Verifier]] = {
    VerificationPoint.V1_TOOL: [],
    VerificationPoint.V2_SUBAGENT: [],
    VerificationPoint.V3_FINAL: [],
}


def register(point: VerificationPoint, verifier: L0Verifier) -> None:
    """按验证点注册一个 L0 检查；重复注册同名函数会被忽略。"""
    bucket = _REGISTRY.setdefault(point, [])
    if verifier not in bucket:
        bucket.append(verifier)


def run_l0(request: VerificationRequest) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    for verifier in _REGISTRY.get(request.point, []):
        try:
            issues.extend(verifier(request))
        except Exception:  # noqa: BLE001 - 单个检查失败不能影响其它检查
            continue
    return issues


def _json_payload(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def check_tool_output(request: VerificationRequest) -> list[VerificationIssue]:
    """V1：工具结果是否可用、是否携带有效证据。"""
    issues: list[VerificationIssue] = []
    text = (request.tool_result or "").strip()
    ref = f"tool_result:{request.tool_name}#{request.tool_call_id}"

    if request.tool_status == "error" or text.startswith("Error:"):
        issues.append(
            VerificationIssue(
                dimension="usability",
                severity="blocking",
                message=f"工具 {request.tool_name} 返回错误",
                evidence_ref=ref,
                suggestion="检查参数与运行环境，必要时换用等价工具",
            )
        )
        return issues
    if not text:
        issues.append(
            VerificationIssue(
                dimension="usability",
                severity="major",
                message=f"工具 {request.tool_name} 返回为空",
                evidence_ref=ref,
                suggestion="确认工具是否真的执行成功，必要时重试或补充参数",
            )
        )
        return issues
    if "已截断" in text:
        issues.append(
            VerificationIssue(
                dimension="detail",
                severity="minor",
                message=f"工具 {request.tool_name} 结果被截断，结论可能缺少上下文",
                evidence_ref=ref,
                suggestion="需要完整内容时改用分页、过滤或读取指定片段",
            )
        )

    if request.tool_name in DATA_TOOLS:
        payload = _json_payload(text)
        if payload is not None:
            count = payload.get("count")
            results = payload.get("results")
            if count == 0 or results == []:
                issues.append(
                    VerificationIssue(
                        dimension="correctness",
                        severity="major",
                        message=f"工具 {request.tool_name} 没有检索到任何结果",
                        evidence_ref=ref,
                        suggestion="换关键词或扩大检索范围；不要据此编造事实",
                    )
                )
    return issues


def check_subagent_output(request: VerificationRequest) -> list[VerificationIssue]:
    """V2：子 Agent 是否给出可用结论、是否越权声称写操作。"""
    issues: list[VerificationIssue] = []
    text = (request.tool_result or "").strip()
    ref = f"subagent:{request.target}"
    if not text:
        return [
            VerificationIssue(
                dimension="completeness",
                severity="blocking",
                message=f"子 Agent {request.target} 返回为空",
                evidence_ref=ref,
                suggestion="重新委派，并在描述里写清期望产出",
            )
        ]
    if len(text) < MIN_TEXT_CHARS:
        issues.append(
            VerificationIssue(
                dimension="completeness",
                severity="major",
                message=f"子 Agent {request.target} 产出过短，疑似未完成",
                evidence_ref=ref,
                suggestion="要求子 Agent 给出结论、依据与未完成项",
            )
        )
    if request.target in READ_ONLY_SUBAGENTS and any(
        token in text for token in WRITE_CLAIM_TOKENS
    ):
        issues.append(
            VerificationIssue(
                dimension="consistency",
                severity="major",
                message=f"只读子 Agent {request.target} 声称执行了写操作",
                evidence_ref=ref,
                suggestion="核对工具轨迹；只读角色不应写入或执行",
            )
        )
    if len(text) > 200 and not any(token in text for token in CONCLUSION_TOKENS):
        issues.append(
            VerificationIssue(
                dimension="completeness",
                severity="minor",
                message=f"子 Agent {request.target} 只有过程，没有明确结论",
                evidence_ref=ref,
                suggestion="要求它在结尾用一句话给出结论",
            )
        )
    return issues


def _mentions(answer: str, text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    if value in answer:
        return True
    grams = {
        value[index : index + 2]
        for index in range(len(value) - 1)
        if value[index : index + 2].strip()
    }
    if not grams:
        return False
    hits = sum(1 for gram in grams if gram in answer)
    return hits / len(grams) >= 0.5


def check_final_answer(request: VerificationRequest) -> list[VerificationIssue]:
    """V3：最终回答非空、覆盖计划项、满足可选 Schema / 数值断言。"""
    issues: list[VerificationIssue] = []
    answer = (request.answer or "").strip()
    if not answer:
        return [
            VerificationIssue(
                dimension="completeness",
                severity="blocking",
                message="最终回答为空",
                evidence_ref="",
                suggestion="重新生成回答",
            )
        ]

    if request.plan_items:
        missing = [
            item
            for item in request.plan_items
            if not _mentions(answer, str(item.get("title") or ""))
            and not _mentions(answer, str(item.get("id") or ""))
        ]
        if missing and len(missing) * 2 >= len(request.plan_items):
            titles = "、".join(
                str(item.get("title") or item.get("id") or "") for item in missing[:4]
            )
            issues.append(
                VerificationIssue(
                    dimension="completeness",
                    severity="major",
                    message=f"回答疑似未覆盖计划项：{titles}",
                    evidence_ref="plan",
                    suggestion="逐项核对计划，补齐缺失项或说明跳过原因",
                )
            )

    return issues


register(VerificationPoint.V1_TOOL, check_tool_output)
register(VerificationPoint.V2_SUBAGENT, check_subagent_output)
register(VerificationPoint.V3_FINAL, check_final_answer)


__all__ = [
    "check_final_answer",
    "check_subagent_output",
    "check_tool_output",
    "register",
    "run_l0",
]
