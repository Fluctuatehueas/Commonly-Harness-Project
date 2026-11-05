"""两层自我裁定中的 L1 自检与验证器调度。"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.constants import TAG_NOSTREAM

from agent.evolution.types import JudgeVerdict, VerifyResult
from agent.evolution.verifiers import Verifier

logger = logging.getLogger(__name__)

_L1_PROMPT = """Audit the final answer before it is sent to the user.

Return only JSON:
{"complete": true/false, "grounded": true/false, "contradiction": true/false,
 "needs_verification": true/false, "reason": "..."}

Definitions:
- complete: does it answer the user's actual request?
- grounded: are key facts from user input or verified tool output, not invented?
- contradiction: any internal contradiction, format error, or missing required field?
- needs_verification: does it contain numbers, dates, units, or claims requiring external checking?

User request:
__USER_TEXT__

Final answer:
__ANSWER__
"""


def _message_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _parse_verdict(content: str) -> JudgeVerdict:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end <= start:
        return JudgeVerdict(
            complete=True,
            grounded=True,
            contradiction=False,
            needs_verification=False,
            reason="自检输出格式无效，按通过处理",
        )
    try:
        payload = json.loads(content[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return JudgeVerdict(
        complete=bool(payload.get("complete", True)),
        grounded=bool(payload.get("grounded", True)),
        contradiction=bool(payload.get("contradiction", False)),
        needs_verification=bool(payload.get("needs_verification", False)),
        reason=str(payload.get("reason") or ""),
    )


class SelfJudge:
    """使用独立摘要模型做结构化自检，并调度 L2 验证器。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def judge_l1(self, *, user_text: str, answer: str) -> JudgeVerdict:
        prompt = _L1_PROMPT.replace("__USER_TEXT__", user_text[:4000]).replace(
            "__ANSWER__", answer[:4000]
        )
        try:
            # nostream：内部自检结果不得进入 SSE 消息流，否则会拼进用户可见回答。
            response = await self._model.ainvoke(prompt, config={"tags": [TAG_NOSTREAM]})
            return _parse_verdict(_message_text(response))
        except Exception:
            logger.warning("L1 自检失败，按通过处理", exc_info=True)
            return JudgeVerdict(
                complete=True,
                grounded=True,
                contradiction=False,
                needs_verification=False,
                reason="自检调用失败",
            )

    async def judge_l2(
        self,
        *,
        answer: str,
        verifiers: list[Verifier],
        context: Any,
    ) -> list[VerifyResult]:
        results: list[VerifyResult] = []
        for verifier in verifiers:
            try:
                results.append(await verifier.verify(answer, context))
            except Exception as error:
                logger.warning("验证器 %s 执行失败", verifier.name, exc_info=True)
                results.append(
                    VerifyResult(False, f"{verifier.name} 执行失败: {error}", fatal=False)
                )
        return results


__all__ = ["SelfJudge"]
