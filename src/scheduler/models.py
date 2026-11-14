"""任务调度数据模型：P0 影子评估 + P1 计划与执行。"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Tier(str, Enum):
    EZ = "EZ"
    MD = "MD"
    HD = "HD"


TIER_ORDER = (Tier.EZ, Tier.MD, Tier.HD)


class FeatureVector(BaseModel):
    """7 维复杂度特征，全部归一化到 [0, 1]。"""

    complexity: float = 0.5
    uncertainty: float = 0.5
    impact: float = 0.5
    scale: float = 0.5
    dep_depth: float = 0.5
    tool_diversity: float = 0.5
    acceptance: float = 0.5

    # 缺失维度名；P0 没有仓库/工具探针，impact 与 tool_diversity 必然缺失。
    missing: list[str] = Field(default_factory=list)
    # 结构性缺失：当前阶段根本没有数据源，而非该任务信息不足。
    # 安全兜底只应针对后者，否则 P0 的动态路径永远无法出现 EZ。
    structural_missing: list[str] = Field(default_factory=list)
    # 每个维度的取值来源，便于审计。
    source: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, str] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        return {
            "complexity": self.complexity,
            "uncertainty": self.uncertainty,
            "impact": self.impact,
            "scale": self.scale,
            "dep_depth": self.dep_depth,
            "tool_diversity": self.tool_diversity,
            "acceptance": self.acceptance,
        }


class TierDecision(BaseModel):
    tier: Tier
    s: float
    probs: dict[str, float]
    rule_override: Optional[str] = None
    upgraded: bool = False
    reason: str = ""


class ShadowRecord(BaseModel):
    """一条影子评估记录。默认不落原文，只落哈希与短预览。"""

    run_id: str
    user_id: str
    mode: str  # reference / dynamic
    flow_id: Optional[str] = None
    # raw_tier 是纯 argmax 结果，tier 是叠加滞回 / 低置信度升档 / 缺失兜底后的最终档位。
    # 两者分开记录，避免安全规则掩盖真实分布，影响影子数据的校准分析。
    raw_tier: Tier
    tier: Tier
    upgraded: bool = False
    reason: str = ""
    s: float
    probs: dict[str, float]
    features: FeatureVector
    message_sha256: str
    message_len: int
    message_preview: str = ""


# ============================================================
# P1 计划与执行
# ============================================================

PlanMode = Literal["reference", "dynamic"]
RunStatus = Literal["created", "running", "paused", "done", "failed", "aborted"]
ItemStatus = Literal["pending", "running", "done", "failed", "skipped", "needs_hitl"]
Level = Literal["low", "mid", "high"]
ReplanScope = Literal["reorder", "subtree", "full"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


class PlanItemInput(BaseModel):
    """模型提交的条目（工具入参）。"""

    id: str = Field(description="条目 ID，例如 p0.1；全局唯一")
    title: str = Field(description="一句话说明要做什么")
    instruction: str = Field(default="", description="执行该条目的完整说明；留空时用 title")
    owner: str = Field(default="auto", description="auto / main / 子 Agent 名称")
    depends_on: list[str] = Field(default_factory=list, description="依赖的条目 ID")
    reads: list[str] = Field(default_factory=list, description="会读取的路径或资源")
    writes: list[str] = Field(default_factory=list, description="会写入的路径或资源")
    acceptance: str = Field(default="", description="完成标准")
    impact: Level = Field(default="mid", description="影响范围：low / mid / high")
    uncertainty: Level = Field(default="mid", description="不确定性：low / mid / high")
    irreversible: bool = Field(default=False, description="是否包含不可逆操作")
    checkpoint: bool = Field(default=False, description="完成后是否强制检查点")


class PlanPhaseInput(BaseModel):
    """模型提交的阶段（工具入参）；MD 只会展开前两个阶段。"""

    id: str = Field(description="阶段 ID，例如 P0")
    title: str = Field(description="阶段名称")
    goal: str = Field(default="", description="阶段目标")
    items: list[PlanItemInput] = Field(default_factory=list, description="阶段下的条目")


class PlanItem(BaseModel):
    id: str
    phase_id: str
    title: str
    instruction: str = ""
    owner: str = "auto"
    depends_on: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    acceptance: str = ""
    impact: Level = "mid"
    uncertainty: Level = "mid"
    irreversible: bool = False
    checkpoint: bool = False
    status: ItemStatus = "pending"
    attempts: int = 0
    summary: str = ""
    error: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class PlanPhase(BaseModel):
    id: str
    title: str
    goal: str = ""
    items: list[PlanItem] = Field(default_factory=list)


class SchedulePlan(BaseModel):
    run_id: str
    goal: str
    mode: PlanMode
    tier: Tier
    plan_version: int = 1
    flow_id: Optional[str] = None
    phases: list[PlanPhase] = Field(default_factory=list)
    # MD 未展开的阶段；执行完当前两个阶段后再由重规划展开。
    pending_phases: list[PlanPhase] = Field(default_factory=list)
    waves: list[list[str]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow_iso)


class Delta(BaseModel):
    """运行期扰动 D：d1 进度、d2 剩余、d3 人工介入、d4 新需求、d5 预算。"""

    d1_progress: float = 0.0
    d2_remaining: int = 0
    d3_manual_intervention: bool = False
    d4_new_requirements: bool = False
    d5_budget_ratio: float = 0.0


class RunState(BaseModel):
    run_id: str
    user_id: str
    thread_id: str = ""
    goal: str = ""
    mode: PlanMode = "dynamic"
    tier: Tier = Tier.MD
    plan_version: int = 1
    status: RunStatus = "created"
    summary: str = ""
    remaining: list[str] = Field(default_factory=list)
    delta: Delta = Field(default_factory=Delta)
    replan_count: int = 0
    item_count: int = 0
    done_count: int = 0
    failed_count: int = 0
    started_at: str = Field(default_factory=utcnow_iso)
    finished_at: Optional[str] = None


class CheckpointDecision(BaseModel):
    score: float
    action: Literal["continue", "reorder", "replan", "hitl"] = "continue"
    scope: Optional[ReplanScope] = None
    reason: str = ""


class RunResult(BaseModel):
    ok: bool
    run_id: str
    status: RunStatus
    plan_version: int = 1
    done: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    summary: str = ""
    checkpoints: int = 0
    replans: int = 0
    error: str = ""
