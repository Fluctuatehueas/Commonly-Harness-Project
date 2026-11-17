"""任务调度数据模型：P0 影子评估 + P1 计划与执行。"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


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
RunStatus = Literal[
    "created",
    "running",
    "paused",
    "done",
    "failed",
    "aborted",
    "cancelled",
    "interrupted",
]
ItemStatus = Literal["pending", "running", "done", "failed", "skipped", "needs_hitl"]
Level = Literal["low", "mid", "high"]
ReplanScope = Literal["reorder", "subtree", "full"]
CheckKind = Literal["file_exists", "field_equals", "regex", "command_exit0"]


class AcceptanceCheck(BaseModel):
    """可机检的验收规则；不填 check 时只交给验收角色语义判断。"""

    kind: CheckKind
    # file_exists / regex / field_equals 用文件路径；command_exit0 用命令
    target: str = ""
    # regex 用正则；field_equals 用 "key=value"（支持 a.b 形式的嵌套键）
    expect: str = ""


class AcceptanceItem(BaseModel):
    text: str = Field(description="一条可判定的验收条件")
    check: Optional[AcceptanceCheck] = Field(
        default=None, description="可机检规则；为空时由验收角色判断"
    )


def acceptance_items(value: Any) -> list[AcceptanceItem]:
    """兼容旧格式：字符串转单条，字符串列表逐条转。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [AcceptanceItem(text=value)]
    if isinstance(value, list):
        items: list[AcceptanceItem] = []
        for entry in value:
            if isinstance(entry, AcceptanceItem):
                items.append(entry)
            elif isinstance(entry, dict):
                items.append(AcceptanceItem.model_validate(entry))
            else:
                items.append(AcceptanceItem(text=str(entry)))
        return items
    return [AcceptanceItem(text=str(value))]


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
    acceptance: list[AcceptanceItem] = Field(
        default_factory=list, description="可判定的完成标准，可带机检规则"
    )
    impact: Level = Field(default="mid", description="影响范围：low / mid / high")
    uncertainty: Level = Field(default="mid", description="不确定性：low / mid / high")
    irreversible: bool = Field(default=False, description="是否包含不可逆操作")
    checkpoint: bool = Field(default=False, description="完成后是否强制检查点")

    @field_validator("acceptance", mode="before")
    @classmethod
    def _coerce_acceptance(cls, value: Any) -> list[AcceptanceItem]:
        return acceptance_items(value)


class PlanMethodInput(BaseModel):
    """模型提交的完成方法：一种把阶段拆成条目的做法。"""

    id: str = Field(description="方法 ID，阶段内唯一，例如 P0.m1")
    title: str = Field(default="", description="方法名称")
    reason: str = Field(default="", description="为什么选这个方法")
    precondition: str = Field(default="", description="方法可用的条件，供选择时判断")
    items: list[PlanItemInput] = Field(default_factory=list, description="该方法下的条目")


class PlanPhaseInput(BaseModel):
    """模型提交的阶段（工具入参）；MD 只会展开前两个阶段。"""

    id: str = Field(description="阶段 ID，例如 P0")
    title: str = Field(description="阶段名称")
    goal: str = Field(default="", description="阶段目标")
    items: list[PlanItemInput] = Field(
        default_factory=list, description="默认方法的条目；提供了 methods 时忽略"
    )
    methods: list[PlanMethodInput] = Field(
        default_factory=list, description="可选方法；第一个为初始方法"
    )


class PlanItem(BaseModel):
    id: str
    phase_id: str
    title: str
    instruction: str = ""
    owner: str = "auto"
    depends_on: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    impact: Level = "mid"
    uncertainty: Level = "mid"
    irreversible: bool = False
    checkpoint: bool = False
    # 不可逆条目的执行授权；只能由 scheduler_replan 的 approve 参数置位
    approved: bool = False
    status: ItemStatus = "pending"
    attempts: int = 0
    summary: str = ""
    error: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @field_validator("acceptance", mode="before")
    @classmethod
    def _coerce_acceptance(cls, value: Any) -> list[AcceptanceItem]:
        # 兼容旧计划：acceptance 曾经是单个字符串
        return acceptance_items(value)


class PlanMethod(BaseModel):
    """阶段的一种完成方法；items 是蓝图，只有被选中时才物化到 phase.items。"""

    id: str
    title: str = ""
    reason: str = ""
    precondition: str = ""
    items: list[PlanItem] = Field(default_factory=list)


class PlanPhase(BaseModel):
    id: str
    title: str
    goal: str = ""
    items: list[PlanItem] = Field(default_factory=list)
    # 候选方法；只有一个时等价于原来的阶段
    methods: list[PlanMethod] = Field(default_factory=list)
    active_method_id: str = ""
    # 已尝试过的方法 ID，用于换方法时跳过
    tried_methods: list[str] = Field(default_factory=list)


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


Verdict = Literal["unverified", "passed", "failed", "waived"]


class FactRecord(BaseModel):
    """S 的一条已完成事实：目标、产出引用、验收结论、证据。"""

    item_id: str
    phase_id: str = ""
    goal: str = ""
    # 产出引用：子图线程号 run_id:item_id，便于回溯轨迹
    output_ref: str = ""
    # 执行器回传的产出摘要，截断后入库
    output_digest: str = ""
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    verdict: Verdict = "unverified"
    # 证据：写入路径等可核对项
    evidence: list[str] = Field(default_factory=list)
    attempts: int = 1
    duration_ms: int = 0
    finished_at: str = ""
    # 同一条目被重做后追加的新事实；旧记录保留
    revised: bool = False

    @field_validator("acceptance", mode="before")
    @classmethod
    def _coerce_acceptance(cls, value: Any) -> list[AcceptanceItem]:
        # 兼容旧事实记录：acceptance 曾经是字符串
        return acceptance_items(value)


class RevisionRecord(BaseModel):
    """重做前的事实级快照：保留旧产出摘要与事实，不复制沙箱文件。"""

    item_id: str
    plan_version: int
    reason: str = ""
    previous_status: ItemStatus = "done"
    previous_summary: str = ""
    previous_finished_at: Optional[str] = None
    # 最近一条事实；没有事实的旧运行允许为空
    snapshot: Optional[FactRecord] = None
    created_at: str = Field(default_factory=utcnow_iso)


class Delta(BaseModel):
    """运行期扰动 D：d1 进度、d2 剩余、d3 人工介入、d4 新需求、d5 上下文占用。"""

    d1_progress: float = 0.0
    d2_remaining: int = 0
    d3_manual_intervention: bool = False
    d4_new_requirements: bool = False
    # 上下文窗口占用比例；其他资源 Agent 拿不到，只有这个信号可信
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
    # 事实账本（S）：唯一来源，summary 只是它渲染出的文本视图
    facts: list[FactRecord] = Field(default_factory=list)
    # 重做快照：只增不改，按时间顺序追加
    revisions: list[RevisionRecord] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    delta: Delta = Field(default_factory=Delta)
    replan_count: int = 0
    # 节流状态：阶段重规划次数、距上次重规划的完成节点数、连续无进展次数
    phase_replans: dict[str, int] = Field(default_factory=dict)
    nodes_since_replan: int = 0
    replans_without_progress: int = 0
    # 运行期降级记录：验收异常、落库失败等，必须让用户看到，不能只写日志
    warnings: list[str] = Field(default_factory=list)
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
    # 实测维度，落库与事件里都要能对上
    impact: str = ""
    uncertainty: str = ""


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
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
