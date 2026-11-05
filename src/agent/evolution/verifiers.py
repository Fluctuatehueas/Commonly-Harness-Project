"""L2 外部验证器。

验证器只做确定性检查，不重新执行有副作用的工具。默认注册表为空；
接入方可通过运行时上下文提供 ``evolution_output_schema`` 或
``evolution_numeric_ranges`` 启用内置验证器。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent.evolution.types import VerifyResult


class Verifier(Protocol):
    name: str

    async def verify(self, answer: str, context: Any) -> VerifyResult:
        ...


class SchemaVerifier:
    """按运行时提供的 JSON Schema 校验结构化输出。"""

    name = "schema"

    async def verify(self, answer: str, context: Any) -> VerifyResult:
        schema = getattr(context, "evolution_output_schema", None)
        if not isinstance(schema, dict) or not schema:
            return VerifyResult(passed=True, detail="未配置输出 Schema")
        start = answer.find("{")
        end = answer.rfind("}")
        if start == -1 or end <= start:
            return VerifyResult(False, "输出中未找到 JSON 对象", fatal=True)
        try:
            payload = json.loads(answer[start : end + 1])
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            return VerifyResult(False, f"JSON 解析失败: {error}", fatal=True)
        required = schema.get("required") or []
        if isinstance(required, list):
            missing = [key for key in required if key not in payload]
            if missing:
                return VerifyResult(False, f"缺少必填字段: {missing}", fatal=True)
        return VerifyResult(True)


class NumericRangeVerifier:
    """按运行时提供的数值区间做确定性断言。"""

    name = "numeric_range"

    async def verify(self, answer: str, context: Any) -> VerifyResult:
        ranges = getattr(context, "evolution_numeric_ranges", None)
        if not isinstance(ranges, dict) or not ranges:
            return VerifyResult(passed=True, detail="未配置数值区间")
        for key, bounds in ranges.items():
            if not isinstance(bounds, dict):
                continue
            if str(key) not in answer:
                continue
            try:
                value = float(bounds.get("value"))
            except (TypeError, ValueError):
                continue
            low = bounds.get("min")
            high = bounds.get("max")
            if low is not None and value < float(low):
                return VerifyResult(False, f"{key} 低于下限 {low}", fatal=True)
            if high is not None and value > float(high):
                return VerifyResult(False, f"{key} 高于上限 {high}", fatal=True)
        return VerifyResult(True)


def build_default_verifiers(context: Any) -> list[Verifier]:
    verifiers: list[Verifier] = []
    if isinstance(getattr(context, "evolution_output_schema", None), dict):
        verifiers.append(SchemaVerifier())
    if isinstance(getattr(context, "evolution_numeric_ranges", None), dict):
        verifiers.append(NumericRangeVerifier())
    return verifiers
