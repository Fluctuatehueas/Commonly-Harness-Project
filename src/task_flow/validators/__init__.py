"""② 数据校验器：Pass 注册与统一执行。

每个 Pass 是 (TaskFlow, ValidationContext) -> list[FlowIssue]，可开关、可单测。
引用存在性检查通过 ValidationContext 注入解析器，便于测试替换；
解析器返回 None 表示"无法确认"（后端不可用），对应 Pass 降级为 warning。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..models import FlowIssue, FlowValidationError, TaskFlow

# 解析器签名：名字 -> True 存在 / False 不存在 / None 无法确认
Resolver = Callable[[str], Optional[bool]]
ValidationPass = Callable[[TaskFlow, "ValidationContext"], list[FlowIssue]]


@dataclass
class ValidationContext:
    skill_exists: Optional[Resolver] = None
    mcp_exists: Optional[Resolver] = None
    role_exists: Optional[Resolver] = None
    # 长度阈值（可被配置覆盖）
    max_name_chars: int = 50
    max_instruction_chars: int = 2000
    max_example_chars: int = 4000
    max_description_chars: int = 2000
    max_parallel_depth: int = 2
    extra: dict = field(default_factory=dict)


_PASSES: dict[str, ValidationPass] = {}
_DISABLED: set[str] = set()


def register(name: str, fn: ValidationPass) -> None:
    _PASSES[name] = fn


def disable(*names: str) -> None:
    _DISABLED.update(names)


def enable(*names: str) -> None:
    for n in names:
        _DISABLED.discard(n)


def registered_passes() -> list[str]:
    return sorted(_PASSES)


def run_all(flow: TaskFlow, ctx: Optional[ValidationContext] = None) -> list[FlowIssue]:
    """跑全部启用的 Pass，返回问题列表（不抛异常）。"""
    ctx = ctx or ValidationContext()
    issues: list[FlowIssue] = []
    for name, fn in _PASSES.items():
        if name in _DISABLED:
            continue
        issues.extend(fn(flow, ctx))
    return issues


def run_all_or_raise(flow: TaskFlow, ctx: Optional[ValidationContext] = None) -> None:
    """存在 error 级问题时抛 FlowValidationError。"""
    issues = run_all(flow, ctx)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise FlowValidationError(errors)


# 导入即注册内置 Pass
from . import safety, structure  # noqa: E402,F401
