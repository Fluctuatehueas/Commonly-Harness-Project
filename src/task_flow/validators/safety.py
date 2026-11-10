"""②a 安全扫描 Pass：提示注入 / 危险指令 / 越权诱导。

威胁模型：用户填写的是自然语言指令，真实注入面是提示注入与越权指令，
而非 SQL 注入（SQL 只读属运行期工具白名单，见 executor）。
模式库内置于此，按 "名称 -> 正则列表" 组织，可直接维护。
"""

from __future__ import annotations

import re

from ..models import FlowIssue, TaskFlow
from . import ValidationContext, register

# 提示注入模式：伪装系统指令、覆盖已有指令、伪造身份
INJECTION_PATTERNS: dict[str, list[str]] = {
    "override_instructions": [
        r"忽略(之前|以上|先前|所有)(的)?.{0,6}(指令|指示|要求|约束)",
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
        r"disregard\s+(all\s+)?(previous|prior)",
        r"不要遵守", r"不必遵守",
    ],
    "fake_system_role": [
        r"你现在是(一个)?(新|另|别的)",
        r"system\s*:\s*you\s+are",
        r"\[\s*system\s*\]",
        r"<\s*\|?\s*(im_start|system)",
        r"扮演.{0,10}(无限制|不受限|越狱)",
    ],
    "prompt_extraction": [
        r"(输出|打印|重复|透露).{0,12}(系统提示|prompt|system\s+prompt|初始指令)",
        r"reveal\s+(your\s+)?(system\s+prompt|instructions?)",
    ],
}

# 危险指令：明确破坏性动作
DANGEROUS_PATTERNS: dict[str, list[str]] = {
    "destructive_ops": [
        r"删除(所有|全部|整个).{0,12}(数据|文件|目录|库|表)",
        r"drop\s+(table|database)", r"truncate\s+table",
        r"rm\s+-rf\s+[/~]", r"format\s+[a-zA-Z]:",
        r"(关停|关闭|终止).{0,8}(服务|进程|系统)",
    ],
}

# 越权诱导：访问他人数据、泄露凭据、绕过权限
PRIVILEGE_PATTERNS: dict[str, list[str]] = {
    "privilege_escalation": [
        r"(访问|读取|导出|下载).{0,12}(其他用户|别的用户|他人|所有用户)的",
        r"(泄露|输出|打印|发送).{0,12}(密钥|密码|token|api[_\s]?key|凭据)",
        r"(绕过|跳过|关闭|禁用).{0,8}(权限|鉴权|审批|安全|校验|白名单)",
        r"bypass\s+(auth|permission|security|approval)",
    ],
}

_COMPILED: list[tuple[str, str, re.Pattern[str]]] = []


def _compile() -> list[tuple[str, str, re.Pattern[str]]]:
    if not _COMPILED:
        for pass_name, groups in (
            ("safety.injection", INJECTION_PATTERNS),
            ("safety.dangerous", DANGEROUS_PATTERNS),
            ("safety.privilege", PRIVILEGE_PATTERNS),
        ):
            for group, patterns in groups.items():
                for p in patterns:
                    _COMPILED.append((pass_name, group, re.compile(p, re.IGNORECASE)))
    return _COMPILED


def _texts(flow: TaskFlow) -> list[tuple[str | None, str, str]]:
    """收集待扫描文本：(step_id, 字段名, 内容)。"""
    items: list[tuple[str | None, str, str]] = [
        (None, "name", flow.name),
        (None, "description", flow.description),
    ]
    for i, t in enumerate(flow.trigger_examples):
        items.append((None, f"trigger_examples[{i}]", t))

    def walk(steps) -> None:
        for step in steps:
            items.extend(
                [
                    (step.id, "name", step.name),
                    (step.id, "instruction", step.instruction),
                    (step.id, "constraints", step.constraints),
                ]
            )
            if step.example:
                items.append((step.id, "example", step.example))
            for sub in step.parallel or []:
                walk(sub.steps)

    walk(flow.steps)
    return items


def _scan(flow: TaskFlow, ctx: ValidationContext) -> list[FlowIssue]:
    issues: list[FlowIssue] = []
    for step_id, field_name, text in _texts(flow):
        if not text:
            continue
        for pass_name, group, pattern in _compile():
            if pattern.search(text):
                issues.append(
                    FlowIssue(
                        pass_name=pass_name,
                        step_id=step_id,
                        message=f"字段 {field_name} 命中{group}模式：{pattern.pattern}",
                    )
                )
    return issues


register("safety.injection", _scan)
