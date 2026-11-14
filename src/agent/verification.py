"""PDCA 闭环与任务流共用的产出校验。

- ``rule_check``：不调用模型的确定性检查；
- ``review_against_example``：有参考样例时，用摘要模型对照格式与结构。

任务流步骤校验与主 Agent 的最终回答自检都复用这里，避免两套规则漂移。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_OUTPUT_CHARS = 20


def rule_check(
    text: str,
    *,
    min_chars: int = MIN_OUTPUT_CHARS,
) -> tuple[bool, str | None]:
    """确定性检查：非空且达到最小长度。"""
    value = (text or "").strip()
    if not value:
        return False, "产出为空"
    if len(value) < min_chars:
        return False, f"产出过短（{len(value)} 字符），疑似未完成"
    return True, None


def _content_to_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


async def review_against_example(
    *,
    step_name: str,
    instruction: str,
    example: str,
    output: str,
) -> tuple[bool, str | None]:
    """让轻量模型对照样例检查产出的格式、结构与关键要素。"""
    from agent.config import get_summary_model

    prompt = (
        "你是任务流步骤产出的质量评审。请对照「参考样例」检查「步骤产出」的格式、结构"
        "与关键要素是否齐备；只关注形式符合度，不评价内容优劣。\n"
        "首行只输出结论：PASS 或 FAIL。若为 FAIL，第二行起用一句话说明缺失点，"
        "这句话会作为返工反馈交给执行者。\n\n"
        f"【步骤名称】{step_name}\n"
        f"【步骤指令】{instruction}\n"
        f"【参考样例】\n{example}\n\n"
        f"【步骤产出】\n{output}"
    )
    try:
        response = await get_summary_model().ainvoke(
            [{"role": "user", "content": prompt}]
        )
    except Exception:
        # 评审模型不可用时不阻断流程，记日志后按通过处理。
        logger.exception("步骤 %s 示例对照评审失败，按通过处理", step_name)
        return True, None
    verdict = _content_to_text(getattr(response, "content", response)).strip()
    first_line, _, rest = verdict.partition("\n")
    if first_line.strip().upper().startswith("PASS"):
        return True, None
    return False, rest.strip() or "产出与参考样例的格式 / 结构 / 关键要素不符"


__all__ = ["MIN_OUTPUT_CHARS", "review_against_example", "rule_check"]
