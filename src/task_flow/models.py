"""任务流核心数据模型（schema v1 / 执行计划 / 执行记录）。

对应设计文档 src/task_flow/README.md 第三章。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"
PLAN_VERSION = "1.0"

FlowSource = Literal["builtin", "user"]
FlowStatus = Literal["draft", "published"]
ExecutionStatus = Literal["running", "done", "failed", "aborted"]
StepTraceStatus = Literal["done", "failed", "degraded", "skipped"]
StateSource = Literal["step", "user_provided"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# 任务流 schema v1
# ============================================================


class Step(BaseModel):
    """步骤：任务流的基本单位。parallel 存在时该步骤为 fork 单元。"""

    id: str
    name: str
    instruction: str = ""
    # "auto" 由 Agent 自行选择；否则为显式技能名列表。
    skills: Literal["auto"] | list[str] = "auto"
    # "main" 主 Agent；"auto" 运行时决定；"subagent:<角色名>" 指定 SubAgent。
    executor: str = "auto"
    constraints: str = ""
    checkpoint: bool = False
    example: Optional[str] = None
    parallel: Optional[list["Subflow"]] = None


class Subflow(BaseModel):
    """并行子流：从主流分叉出的并行执行线。"""

    name: str
    steps: list[Step] = Field(default_factory=list)


Step.model_rebuild()


class TaskFlow(BaseModel):
    """任务流 schema v1。"""

    schema_version: str = SCHEMA_VERSION
    id: str
    name: str
    description: str = ""
    source: FlowSource = "user"
    status: FlowStatus = "draft"
    trigger_examples: list[str] = Field(default_factory=list)
    mcp_services: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


# ============================================================
# 执行计划（编译产物）
# ============================================================


class ExecutionUnit(BaseModel):
    """执行单元：step 或 fork。fork 的分支内嵌各自的单元数组（递归）。"""

    seq: int
    type: Literal["step", "fork"]
    ref: str  # 对应步骤 id
    executor: str = "auto"
    checkpoint: bool = False
    branches: Optional[list["ForkBranch"]] = None
    join: Optional[Literal["all"]] = None


class ForkBranch(BaseModel):
    name: str
    units: list[ExecutionUnit] = Field(default_factory=list)


ExecutionUnit.model_rebuild()


class ExecutionPlan(BaseModel):
    plan_version: str = PLAN_VERSION
    flow_id: str
    schema_hash: str
    units: list[ExecutionUnit] = Field(default_factory=list)


# ============================================================
# 校验问题
# ============================================================


class FlowIssue(BaseModel):
    """单条校验问题；step_id 供前端就地标注。"""

    pass_name: str
    step_id: Optional[str] = None
    level: Literal["error", "warning"] = "error"
    message: str


class FlowValidationError(Exception):
    """编译期校验失败，携带全部 FlowIssue。"""

    def __init__(self, issues: list[FlowIssue]):
        self.issues = issues
        super().__init__(
            "任务流校验失败：\n" + "\n".join(f"- [{i.pass_name}] {i.message}" for i in issues)
        )


# ============================================================
# 执行记录
# ============================================================


class StepTrace(BaseModel):
    seq: int
    step_id: str
    branch: Optional[str] = None  # fork 分支名；主流步骤为 None
    status: StepTraceStatus
    attempts: int = 1
    input_summary: str = ""
    output_summary: str = ""
    # 完整产出（受 EXECUTION_OUTPUT_MAX_CHARS 上限约束），供执行记录回放；
    # output_summary 只保留前 200 字，用于卡片等轻量场景。
    output: str = ""
    duration_ms: int = 0
    check_result: Optional[str] = None
    state_source: StateSource = "step"


class Execution(BaseModel):
    id: str
    flow_id: str
    user_id: str
    thread_id: str = ""
    schema_hash: str
    entry_step: Optional[str] = None  # 中途接管入口步骤
    status: ExecutionStatus = "running"
    step_traces: list[StepTrace] = Field(default_factory=list)
    trace_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


def new_flow_id() -> str:
    import uuid

    return f"tf_{uuid.uuid4().hex[:12]}"


def new_execution_id() -> str:
    import uuid

    return f"ex_{uuid.uuid4().hex[:12]}"
