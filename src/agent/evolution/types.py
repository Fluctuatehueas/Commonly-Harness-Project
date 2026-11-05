"""演进模块的公共数据模型。

这里只放跨模块复用的数据结构；具体读写逻辑放在各自模块中。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class RiskTier(str, Enum):
    """任务风险等级，决定自我裁定与晋升强度。"""

    T0 = "t0"
    T1 = "t1"
    T2 = "t2"


class ReplayTier(str, Enum):
    """轨迹回放等级。"""

    EXACT = "exact"
    APPROX = "approx"
    NONE = "none"


class InterventionLayer(str, Enum):
    """可干预面。"""

    MEMORY = "memory"
    EXPERIENCE = "experience"
    HARNESS = "harness"
    TOOL_SCHEMA = "tool_schema"
    TOOL_ROUTER = "tool_router"
    SKILL = "skill"
    MODEL_WEIGHT = "model_weight"
    ROLLBACK = "rollback"
    NOOP = "noop"


class AdmissionState(str, Enum):
    """经验条目的准入状态。"""

    PROPOSED = "proposed"
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReleaseStatus(str, Enum):
    """演进版本状态。"""

    PROPOSED = "proposed"
    APPROVED = "approved"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


@dataclass
class VersionManifest:
    """一次 Agent run 的版本快照，用于回放与归因。"""

    harness_version: str = "dev"
    prompt_version: str = ""
    skill_generation: int = 0
    tool_schema_version: str = ""
    model_name: str = ""
    retrieval_snapshot: str = ""
    config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RewardRecord:
    """奖励账本中的一条记录。"""

    reward_id: str
    execution_id: str
    event_id: str
    thread_id: str
    user_id: str
    tenant_id: str
    source: str
    value: float
    confidence: float
    category: str
    evidence: str = ""
    delayed: bool = False
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperienceRecord:
    """Agent 从轨迹中提炼的一条可复用经验。"""

    experience_id: str
    scope: str
    kind: Literal["success_pattern", "failure_trigger", "contrast"]
    trigger: str
    rule: str
    evidence_refs: list[str]
    utility: float
    utility_observations: int
    confidence: float
    provenance: str
    admission_state: AdmissionState
    generation: int
    tags: list[str]
    ttl_days: int
    created_at: str
    updated_at: str
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["admission_state"] = self.admission_state.value
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExperienceRecord":
        data = dict(value)
        data["admission_state"] = AdmissionState(
            data.get("admission_state", AdmissionState.PROPOSED.value)
        )
        data["evidence_refs"] = list(data.get("evidence_refs") or [])
        data["tags"] = list(data.get("tags") or [])
        return cls(**data)


@dataclass
class ToolHealthRecord:
    """单个工具的健康度累计。"""

    server_id: str
    tool_name: str
    criticality: str
    samples: int
    successes: int
    tool_errors: int
    infra_errors: int
    latencies: list[int]
    reward_ema: float
    last_error_category: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.samples, 1)

    @property
    def latency_p50(self) -> int:
        return _percentile(self.latencies, 0.5)

    @property
    def latency_p95(self) -> int:
        return _percentile(self.latencies, 0.95)


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(max(int(len(ordered) * ratio) - 1, 0), len(ordered) - 1)
    return int(ordered[index])


@dataclass
class Attribution:
    """一次失败归因的结果。"""

    attribution_id: str
    execution_id: str
    signal: str
    layer: InterventionLayer
    target: str
    reason: str
    confidence: float
    high_uncertainty: bool
    evidence_refs: list[str]
    actor_layer: str = ""
    observer_layer: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["layer"] = self.layer.value
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Attribution":
        data = dict(value)
        data["layer"] = InterventionLayer(data.get("layer", InterventionLayer.NOOP.value))
        return cls(**data)


@dataclass
class InterventionCandidate:
    """控制面生成的干预候选。"""

    candidate_id: str
    user_id: str
    tenant_id: str
    layer: InterventionLayer
    target: str
    change_spec: dict[str, Any]
    confidence: float
    evidence_refs: list[str]
    risk_tier: RiskTier
    requires_human_approval: bool
    status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["layer"] = self.layer.value
        data["risk_tier"] = self.risk_tier.value
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InterventionCandidate":
        data = dict(value)
        data["layer"] = InterventionLayer(data.get("layer", InterventionLayer.NOOP.value))
        data["risk_tier"] = RiskTier(data.get("risk_tier", RiskTier.T0.value))
        data["change_spec"] = dict(data.get("change_spec") or {})
        data["evidence_refs"] = list(data.get("evidence_refs") or [])
        return cls(**data)


@dataclass
class ReleaseRecord:
    """一次版本变更与晋升状态。"""

    release_id: str
    tenant_id: str
    user_id: str
    layer: InterventionLayer
    target: str
    artifact_version: str
    previous_version: str
    status: ReleaseStatus
    evidence_refs: list[str]
    impact_scope: dict[str, Any]
    rollback_plan: str
    approved_by: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["layer"] = self.layer.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReleaseRecord":
        data = dict(value)
        data["layer"] = InterventionLayer(data.get("layer", InterventionLayer.NOOP.value))
        data["status"] = ReleaseStatus(data.get("status", ReleaseStatus.PROPOSED.value))
        data["evidence_refs"] = list(data.get("evidence_refs") or [])
        data["impact_scope"] = dict(data.get("impact_scope") or {})
        return cls(**data)


@dataclass
class VerifyResult:
    """外部验证器结果。"""

    passed: bool
    detail: str = ""
    fatal: bool = False


@dataclass
class JudgeVerdict:
    """L1 自检结论。"""

    complete: bool
    grounded: bool
    contradiction: bool
    needs_verification: bool
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.complete and self.grounded and not self.contradiction


@dataclass
class RunSignals:
    """一次 run 结束后用于学习与归因的弱信号。"""

    has_failure: bool = False
    tool_errors: list[dict[str, Any]] = field(default_factory=list)
    verification_failed: bool = False
    hitl_rejected: bool = False
    user_correction: bool = False
    repeated_steps: int = 0
    injected_experience_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
