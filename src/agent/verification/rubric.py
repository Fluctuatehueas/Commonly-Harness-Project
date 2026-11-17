"""L1 判官提示词与结构化结果解析。

判官只输出 JSON；无法解析时调用方按 degraded 处理，不做猜测。
"""

from __future__ import annotations

import json
from typing import Any

from agent.verification.evidence import format_evidence_for_prompt
from agent.verification.types import (
    VerificationIssue,
    VerificationRequest,
)

MAX_FIELD_CHARS = 4000
VALID_VERDICTS = {"pass", "warn", "fail"}
VALID_SEVERITIES = {"blocking", "major", "minor"}

JUDGE_SYSTEM_PROMPT = """你是 Agent 产出的质量验证员。只根据「任务」「产出」「证据清单」判断，
不使用外部知识，不补充产出里没有的事实。

判断顺序：
1. 完整性：任务要求与计划项是否逐项覆盖。
2. 正确性：结论是否与证据一致；数字、单位、计算是否自洽。
3. 一致性：同一事实在产出内是否前后一致，是否与工具结果冲突。
4. 可解释性：每个关键结论能否对应到证据清单；没有证据的结论视为编造。

输出只允许 JSON：
{"verdict":"pass|warn|fail","scores":{"correctness":0,"completeness":0,"consistency":0,"interpretability":0},
 "issues":[{"dimension":"correctness|completeness|consistency|usability|interpretability|detail",
 "severity":"blocking|major|minor","message":"一句话","evidence_ref":"证据引用","suggestion":"最小修复动作"}],
 "confidence":0,"needs_deeper_review":false}

规则：
- 只有存在 blocking 问题时才能给 fail；无法判断时给 warn。
- 关键结论对不上证据清单，记 interpretability/blocking。
- 不输出寒暄、过程或改写后的正文。"""


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}…[已截断]"


def _plan_text(request: VerificationRequest) -> str:
    if request.plan_items:
        return "\n".join(
            f"- {item.get('id') or '-'}：{item.get('title') or ''}"
            for item in request.plan_items
        )
    return "（本轮没有分工计划）"


def build_judge_prompt(request: VerificationRequest) -> str:
    """构造 L1 判官的完整用户消息。"""
    return (
        f"【任务】\n{_clip(request.user_text)}\n\n"
        f"【计划项】\n{_plan_text(request)}\n\n"
        f"【产出】\n{_clip(request.answer)}\n\n"
        f"【证据清单】\n{format_evidence_for_prompt(request.evidence)}"
    )


def build_critic_prompt(
    request: VerificationRequest,
    issues: list[VerificationIssue],
) -> str:
    """构造批判者的输入：只针对已发现的问题，不重新打分。"""
    issue_lines = "\n".join(
        f"- [{issue.severity}/{issue.dimension}] {issue.message} "
        f"(证据：{issue.evidence_ref or '无'})"
        for issue in issues
    )
    return (
        f"【任务】\n{_clip(request.user_text)}\n\n"
        f"【产出】\n{_clip(request.answer)}\n\n"
        f"【已发现问题】\n{issue_lines}\n\n"
        f"【证据清单】\n{format_evidence_for_prompt(request.evidence)}"
    )


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取第一个 JSON 对象；失败返回 None。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _scores(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("scores")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for key in (
        "correctness",
        "completeness",
        "consistency",
        "interpretability",
    ):
        try:
            result[key] = round(float(raw.get(key)), 3)
        except (TypeError, ValueError):
            continue
    return result


def _issues(payload: dict[str, Any]) -> list[VerificationIssue]:
    raw = payload.get("issues")
    if not isinstance(raw, list):
        return []
    issues: list[VerificationIssue] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "minor").lower()
        issues.append(
            VerificationIssue(
                dimension=str(item.get("dimension") or "correctness"),
                severity=severity if severity in VALID_SEVERITIES else "minor",
                message=str(item.get("message") or "").strip(),
                evidence_ref=str(item.get("evidence_ref") or "").strip(),
                suggestion=str(item.get("suggestion") or "").strip(),
            )
        )
    return [issue for issue in issues if issue.message]


def normalize_payload(
    payload: dict[str, Any],
) -> tuple[str, list[VerificationIssue], dict[str, float], bool]:
    """把判官输出归一为（verdict, issues, scores, needs_deeper_review）。

    判定以 issue 条目为准：有 blocking 即 fail，有其它问题即 warn，
    完全不把模型自报的 verdict 当作唯一依据。
    """
    issues = _issues(payload)
    scores = _scores(payload)
    has_blocking = any(issue.severity == "blocking" for issue in issues)
    if has_blocking:
        verdict = "fail"
    elif issues:
        verdict = "warn"
    else:
        raw_verdict = str(payload.get("verdict") or "pass").lower()
        verdict = raw_verdict if raw_verdict in VALID_VERDICTS else "pass"
        if verdict == "fail":
            # 没有 blocking 条目的 fail 降级为 warn，避免空泛否定。
            verdict = "warn"
    needs_deeper = bool(payload.get("needs_deeper_review", False))
    return verdict, issues, scores, needs_deeper


__all__ = [
    "JUDGE_SYSTEM_PROMPT",
    "build_critic_prompt",
    "build_judge_prompt",
    "extract_json",
    "normalize_payload",
]
