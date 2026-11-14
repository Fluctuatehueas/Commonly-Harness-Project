"""P0 特征抽取：只做确定性启发式，不调用 LLM、不扫描仓库。

覆盖文本可得的维度（复杂度、不确定性、规模、验收严格度），
影响范围、工具多样性在 P0 固定缺失，由 TierConfig 的缺失惩罚处理。
"""

import math
import re
from typing import Any, Optional

from .models import FeatureVector

_CLAUSE_RE = re.compile(r"[,，;；。.!?！？\n]+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CONSTRAINT_RE = re.compile(
    r"(必须|务必|只能|不得|至少|至多|上限|下限|在.{0,6}之前|"
    r"\bmust\b|\bonly\b|\bwithin\b|\bat most\b|\bat least\b|\bno more than\b|\bwithout\b)",
    re.IGNORECASE,
)
_VAGUE_RE = re.compile(
    r"(大概|可能|也许|差不多|随便|看看|优化一下|改一下|处理一下|之类的|等等|"
    r"\bmaybe\b|\bprobably\b|\broughly\b|\bsomehow\b|\bimprove\b|\bsome\b|\betc\.?\b)",
    re.IGNORECASE,
)
_ACCEPTANCE_RE = re.compile(
    r"(测试|验收|单测|集成测试|发布|上线|回归|通过标准|"
    r"\btest\b|\bacceptance\b|\bverify\b|\bdeploy\b|\brelease\b|\bci\b)",
    re.IGNORECASE,
)


def _clamp(value: float, low: float = 0.02, high: float = 0.98) -> float:
    return max(low, min(high, value))


def _text_stats(message: str) -> dict[str, float]:
    text = message or ""
    clauses = [c for c in _CLAUSE_RE.split(text) if c.strip()]
    words = _WORD_RE.findall(text)
    return {
        "chars": float(len(text)),
        "clauses": float(max(1, len(clauses))),
        "words": float(len(words)),
        "questions": float(text.count("?") + text.count("？")),
        "constraints": float(len(_CONSTRAINT_RE.findall(text))),
        "vague": float(len(_VAGUE_RE.findall(text))),
        "acceptance": 1.0 if _ACCEPTANCE_RE.search(text) else 0.0,
    }


def _flow_stats(flow: Any) -> dict[str, float]:
    """从 TaskFlow 提取步骤、并行子流与检查点数量；失败时返回空统计。"""
    if flow is None:
        return {}
    try:
        steps = list(getattr(flow, "steps", []) or [])
        total = 0
        subflows = 0
        checkpoints = 0
        constrained = 0
        for step in steps:
            total += 1
            if getattr(step, "checkpoint", False):
                checkpoints += 1
            if getattr(step, "constraints", ""):
                constrained += 1
            branches = getattr(step, "parallel", None) or []
            subflows += len(branches)
            for branch in branches:
                total += len(getattr(branch, "steps", []) or [])
        return {
            "steps": float(total),
            "subflows": float(subflows),
            "checkpoints": float(checkpoints),
            "constrained": float(constrained),
        }
    except Exception:
        return {}


def _plan_depth(plan: Any) -> Optional[int]:
    """执行计划的最大嵌套深度，作为依赖深度的代理。"""
    if plan is None:
        return None

    def walk(units: Any) -> int:
        depth = 0
        for unit in units or []:
            depth = max(depth, 1)
            for branch in getattr(unit, "branches", None) or []:
                depth = max(depth, 1 + walk(getattr(branch, "units", None)))
        return depth

    try:
        return walk(getattr(plan, "units", None))
    except Exception:
        return None


def extract_features(message: str, flow: Any = None, plan: Any = None) -> FeatureVector:
    """按文本与任务流抽取 7 维特征。

    P0 约定：
    - impact / tool_diversity 没有数据源，固定 0.5 并记入 missing；
    - 缺少 plan 时 dep_depth 同样记缺失；
    - 所有维度落在 [0.02, 0.98]，避免几何平均被 0 吞掉。
    """
    text = _text_stats(message)
    fs = _flow_stats(flow)
    depth = _plan_depth(plan)

    has_reference = flow is not None
    n_units = fs.get("steps", text["clauses"])
    n_units = max(1.0, n_units)

    missing: list[str] = []
    # P0 的结构性缺失：没有数据源，而不是该任务缺信息。安全兜底需要区分二者。
    structural: list[str] = ["impact", "tool_diversity"]
    source: dict[str, str] = {}
    evidence: dict[str, str] = {}

    complexity = 1 - math.exp(-n_units / 5)
    source["complexity"] = "flow" if has_reference else "text"
    evidence["complexity"] = f"units={n_units:.0f}"

    uncertainty = 0.15 if has_reference else 0.45
    vague_ratio = min(1.0, text["vague"] / max(1.0, text["clauses"]))
    uncertainty += 0.25 * vague_ratio
    uncertainty += 0.10 if text["questions"] > 0 else 0.0
    uncertainty += 0.15 if text["chars"] < 30 else 0.0
    source["uncertainty"] = "flow+text" if has_reference else "text"
    evidence["uncertainty"] = f"vague={text['vague']:.0f}, questions={text['questions']:.0f}"

    impact = 0.5
    missing.append("impact")
    source["impact"] = "default:missing"
    evidence["impact"] = "P0 无仓库/影响面探针"

    scale = 1 - math.exp(-n_units / 8)
    source["scale"] = "flow" if has_reference else "text"
    evidence["scale"] = f"units={n_units:.0f}"

    if depth is None:
        dep_depth = 0.5
        missing.append("dep_depth")
        # 动态路径在执行计划生成前本来就没有 plan，属于结构性缺失。
        structural.append("dep_depth")
        source["dep_depth"] = "default:missing"
        evidence["dep_depth"] = "无执行计划"
    else:
        dep_depth = 1 - math.exp(-depth / 4)
        source["dep_depth"] = "plan"
        evidence["dep_depth"] = f"depth={depth}"

    tool_diversity = 0.5
    missing.append("tool_diversity")
    source["tool_diversity"] = "default:missing"
    evidence["tool_diversity"] = "P0 未读取工具集"

    if has_reference:
        checkpoint_ratio = fs.get("checkpoints", 0.0) / max(1.0, n_units)
        acceptance = 0.35 + 0.25 * min(1.0, checkpoint_ratio) + (0.10 if fs.get("constrained", 0) else 0.0)
        source["acceptance"] = "flow"
    else:
        acceptance = 0.25 + 0.25 * text["acceptance"]
        source["acceptance"] = "text"
    evidence["acceptance"] = f"checkpoints={fs.get('checkpoints', 0):.0f}, words={text['acceptance']:.0f}"

    return FeatureVector(
        complexity=_clamp(complexity),
        uncertainty=_clamp(uncertainty),
        impact=_clamp(impact),
        scale=_clamp(scale),
        dep_depth=_clamp(dep_depth),
        tool_diversity=_clamp(tool_diversity),
        acceptance=_clamp(acceptance),
        missing=missing,
        structural_missing=structural,
        source=source,
        evidence=evidence,
    )
