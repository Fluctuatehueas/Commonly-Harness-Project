"""验证模块的公共数据模型。

验证是纯观测能力：这里的数据结构只用于记录结论、落轨迹与回流信号，
不参与回答放行、不改写工具结果。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class VerificationPoint(str, Enum):
    """主链路上的三个验证点。"""

    V1_TOOL = "v1_tool"
    V2_SUBAGENT = "v2_subagent"
    V3_FINAL = "v3_final"


class VerificationLevel(str, Enum):
    """验证强度：规则 / 轻量判官 / 委派验证。"""

    L0 = "l0"
    L1 = "l1"
    L2 = "l2"


@dataclass
class VerificationIssue:
    """验证发现的一条问题。"""

    dimension: str
    severity: str
    message: str
    evidence_ref: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VerificationIssue":
        return cls(
            dimension=str(value.get("dimension") or "correctness"),
            severity=str(value.get("severity") or "minor"),
            message=str(value.get("message") or ""),
            evidence_ref=str(value.get("evidence_ref") or ""),
            suggestion=str(value.get("suggestion") or ""),
        )


@dataclass
class VerificationRequest:
    """一次验证请求；所有验证点共用一个入口。"""

    point: VerificationPoint
    level: VerificationLevel
    target: str
    answer: str
    user_id: str = ""
    thread_id: str = ""
    run_id: str = ""
    user_text: str = ""
    plan: str = ""
    plan_items: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    tool_name: str = ""
    tool_args: str = ""
    tool_result: str = ""
    tool_status: str = ""
    tool_call_id: str = ""
    # PDCA 结论快照：后台执行时避免被下一轮重置。
    pdc_outcome: str = ""
    pdc_reason: str = ""
    context: Any = None
    store: Any = None


@dataclass
class VerificationRecord:
    """一次验证的完整结论，用于落轨迹与信号回流。"""

    verification_id: str
    run_id: str
    thread_id: str
    point: str
    level: str
    target: str
    verdict: str
    issues: list[VerificationIssue] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    judge_model: str = ""
    latency_ms: int = 0
    tokens: dict[str, int] = field(default_factory=dict)
    source: str = "l0"
    created_at: str = ""

    @property
    def blocking_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "blocking")

    def preview(self) -> str:
        if not self.issues:
            return "未发现问题"
        first = self.issues[0]
        extra = f"（另有 {len(self.issues) - 1} 条）" if len(self.issues) > 1 else ""
        return f"{first.message}{extra}"

    def label(self) -> str:
        point_label = {
            VerificationPoint.V1_TOOL.value: "工具产出",
            VerificationPoint.V2_SUBAGENT.value: "子 Agent 返回",
            VerificationPoint.V3_FINAL.value: "最终回答",
        }.get(self.point, self.point)
        verdict_label = {
            "pass": "通过",
            "warn": "有提示",
            "fail": "发现阻断问题",
            "degraded": "降级",
            "skipped": "跳过",
        }.get(self.verdict, self.verdict)
        return f"验证{point_label}：{self.target or '-'}（{verdict_label}）"

    def to_event(self) -> dict[str, Any]:
        """转成轨迹事件；type 固定为 verification。"""
        now = self.created_at or datetime.now(timezone.utc).isoformat()
        summary = {
            "point": self.point,
            "level": self.level,
            "verdict": self.verdict,
            "source": self.source,
            "judge_model": self.judge_model,
            "blocking": self.blocking_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "scores": self.scores,
            "evidence_refs": self.evidence_refs,
            "degraded": self.verdict == "degraded",
        }
        return {
            "id": self.verification_id,
            "run_id": self.run_id,
            "parent_id": "",
            "graph": "main",
            "type": "verification",
            "label": self.label(),
            "status": "done",
            "start": now,
            "end": now,
            "duration_ms": self.latency_ms,
            "context": {},
            "preview": self.preview(),
            "result": self.preview(),
            "args": "",
            "tokens": self.tokens,
            "tool_name": self.target if self.point == VerificationPoint.V1_TOOL.value else "",
            "tool_call_id": "",
            "subagent_name": (
                self.target if self.point == VerificationPoint.V2_SUBAGENT.value else ""
            ),
            "source": "verification",
            "summary": summary,
            "verification": summary,
        }


def new_verification_id() -> str:
    return f"ver_{uuid4().hex[:16]}"
