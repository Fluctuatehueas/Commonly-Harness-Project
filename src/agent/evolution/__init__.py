"""Agent 自我迭代与演进模块。

所有模块默认开启，作为系统底层规则引擎运行；需要做效果对比时可分别
通过 ``HARNESS_EVOLUTION_*`` 环境变量关闭。
"""

from agent.evolution.types import (
    AdmissionState,
    Attribution,
    ExperienceRecord,
    InterventionCandidate,
    InterventionLayer,
    ReplayTier,
    RewardRecord,
    RiskTier,
    ToolHealthRecord,
    VersionManifest,
)

__all__ = [
    "AdmissionState",
    "Attribution",
    "ExperienceRecord",
    "InterventionCandidate",
    "InterventionLayer",
    "ReplayTier",
    "RewardRecord",
    "RiskTier",
    "ToolHealthRecord",
    "VersionManifest",
]
