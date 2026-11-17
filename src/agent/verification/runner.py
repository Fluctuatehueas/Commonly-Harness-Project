"""验证执行入口（纯观测）。

所有验证点共用同一套预算、超时、降级与落库逻辑：

* V1 / V2：L0 规则，V2 可按抽样率补一次轻量判官；
* V3：优先复用本轮 PDCA 结论；没有结论时后台补跑 L1；
* L2：配置开启且出现 blocking 时，用验证子 Agent 的提示词做一次深评。

验证结论只写轨迹与信号，不参与回答放行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from langgraph.constants import TAG_NOSTREAM

from agent.config import (
    EVOLUTION_JUDGE_RISK_TIER,
    JUDGE_TIMEOUT,
    VERIFICATION_ASYNC,
    VERIFICATION_ENABLED,
    VERIFICATION_L1_SAMPLE_RATE,
    VERIFICATION_L2_ENABLED,
    VERIFICATION_L2_RISK_TIER,
    VERIFICATION_MAX_LEVEL,
    VERIFICATION_POINTS,
)
from agent.trajectory_store import append_trajectory_event

import agent.verification.budget as budget
import agent.verification.l0 as l0
from agent.verification.evidence import (
    message_text,
    risk_tier_from_names,
)
from agent.verification.rubric import (
    build_critic_prompt,
    build_judge_prompt,
    extract_json,
    normalize_payload,
)
from agent.verification.types import (
    VerificationIssue,
    VerificationLevel,
    VerificationPoint,
    VerificationRecord,
    VerificationRequest,
    new_verification_id,
)

logger = logging.getLogger(__name__)

_LEVEL_ORDER = {
    VerificationLevel.L0.value: 0,
    VerificationLevel.L1.value: 1,
    VerificationLevel.L2.value: 2,
}
_TIER_ORDER = {"t0": 0, "t1": 1, "t2": 2}


def point_enabled(point: VerificationPoint) -> bool:
    return VERIFICATION_ENABLED and point.value in VERIFICATION_POINTS


def _max_level() -> int:
    return _LEVEL_ORDER.get(str(VERIFICATION_MAX_LEVEL), 2)


def _verdict(issues: list[VerificationIssue]) -> str:
    if any(issue.severity == "blocking" for issue in issues):
        return "fail"
    if issues:
        return "warn"
    return "pass"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage(response: Any) -> dict[str, int]:
    """读取模型返回的 Token 用量，兼容两种字段命名。"""
    usage = getattr(response, "usage_metadata", None) or {}
    if not isinstance(usage, dict) or not usage:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = metadata.get("token_usage") if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        return {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total = usage.get("total_tokens")
    if total is None and (input_tokens is not None or output_tokens is not None):
        total = int(input_tokens or 0) + int(output_tokens or 0)
    return {
        key: int(value)
        for key, value in {
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
        }.items()
        if value is not None
    }


def _model_name(model: Any) -> str:
    return str(
        getattr(model, "model_name", "")
        or getattr(model, "model", "")
        or model.__class__.__name__
    )


class VerificationRunner:
    """验证统一入口。"""

    async def run(self, request: VerificationRequest) -> VerificationRecord | None:
        if not point_enabled(request.point):
            return None

        started_at = time.perf_counter()
        issues = l0.run_l0(request)
        verdict = _verdict(issues)
        scores: dict[str, float] = {}
        judge_model = ""
        tokens: dict[str, int] = {}
        source = "l0"
        level = VerificationLevel.L0
        tool_names = [
            str(item.get("name") or "")
            for item in request.evidence
            if item.get("kind") == "tool_call"
        ]
        tier = risk_tier_from_names(tool_names, request.answer)

        if request.point is VerificationPoint.V3_FINAL:
            issues.extend(await self._run_async_verifiers(request))
            verdict = _verdict(issues)

            reused = self._reuse_pdc(request)
            if reused is not None:
                verdict, reused_issues, scores, source = reused
                if reused_issues:
                    issues = reused_issues
                # PDCA 通过也不能掩盖 L0 已经发现的问题。
                if verdict == "pass":
                    verdict = _verdict(issues)
                level = VerificationLevel.L1
            elif self._should_judge(request) and budget.can_call_judge(
                request.context
            ):
                (
                    verdict,
                    judged_issues,
                    scores,
                    tokens,
                    judge_model,
                    degraded,
                ) = await self._call_judge(request)
                if judged_issues:
                    issues = judged_issues
                source = "degraded" if degraded else "l1"
                level = VerificationLevel.L1
        elif request.point is VerificationPoint.V2_SUBAGENT:
            if (
                _max_level() >= 1
                and budget.sample_hit(VERIFICATION_L1_SAMPLE_RATE)
                and budget.can_call_judge(request.context)
            ):
                (
                    verdict,
                    judged_issues,
                    scores,
                    tokens,
                    judge_model,
                    degraded,
                ) = await self._call_judge(request)
                if judged_issues:
                    issues = judged_issues
                source = "degraded" if degraded else "l1"
                level = VerificationLevel.L1

        # 最终产出按风险等级自动深评：达到 T1 就跑一次 L2，不再要求先出现 fail。
        l2_by_risk = (
            request.point is VerificationPoint.V3_FINAL
            and _TIER_ORDER.get(tier, 0)
            >= _TIER_ORDER.get(str(VERIFICATION_L2_RISK_TIER), 1)
        )
        if (
            (verdict == "fail" or l2_by_risk)
            and VERIFICATION_L2_ENABLED
            and _max_level() >= 2
            and budget.can_delegate(request.context)
        ):
            issues, extra_tokens, delegated = await self._delegate(request, issues)
            verdict = _verdict(issues)
            tokens = _merge_tokens(tokens, extra_tokens)
            source = "l2" if delegated else "l2_degraded"
            level = VerificationLevel.L2

        record = VerificationRecord(
            verification_id=new_verification_id(),
            run_id=request.run_id,
            thread_id=request.thread_id,
            point=request.point.value,
            level=level.value,
            target=request.target,
            verdict=verdict,
            issues=issues,
            scores=scores,
            evidence_refs=[str(item.get("ref") or "") for item in request.evidence if item.get("ref")],
            judge_model=judge_model,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            tokens=tokens,
            source=source,
            created_at=_now(),
        )
        await self._write_event(request, record)
        self._apply_signals(request.context, record)
        await self._record_reward(request, record)
        return record

    # ---------- 复用与判官 ----------

    @staticmethod
    def _reuse_pdc(request: VerificationRequest) -> tuple[str, list[VerificationIssue], dict[str, float], str] | None:
        """复用本轮 PDCA 自检结论，避免重复调用判官。"""
        outcome = request.pdc_outcome or str(
            getattr(request.context, "pdc_outcome", "") or ""
        )
        if outcome not in {"passed", "exhausted"}:
            return None
        if outcome == "passed":
            return "pass", [], {}, "reuse_pdc"
        reason = request.pdc_reason or str(
            getattr(request.context, "evolution_verification_reason", "") or ""
        )
        return (
            "fail",
            [
                VerificationIssue(
                    dimension="correctness",
                    severity="blocking",
                    message=reason or "PDCA 自检未通过",
                    evidence_ref="pdc",
                    suggestion="按自检原因补充证据或修正结论",
                )
            ],
            {},
            "reuse_pdc",
        )

    def _should_judge(self, request: VerificationRequest) -> bool:
        """V3 是否运行 L1：按风险等级，与 PDCA 的阈值保持一致。"""
        threshold = _TIER_ORDER.get(str(EVOLUTION_JUDGE_RISK_TIER), 1)
        names = [
            str(item.get("name") or "")
            for item in request.evidence
            if item.get("kind") == "tool_call"
        ]
        tier = risk_tier_from_names(names, request.answer)
        return _TIER_ORDER.get(tier, 0) >= threshold

    async def _call_judge(
        self, request: VerificationRequest
    ) -> tuple[str, list[VerificationIssue], dict[str, float], dict[str, int], str, bool]:
        """调用一次轻量判官；失败或超时统一返回 degraded。"""
        from agent.config import get_judge_model

        budget.note_judge_call(request.context)
        try:
            model = get_judge_model(request.user_id)
            response = await asyncio.wait_for(
                model.ainvoke(
                    build_judge_prompt(request),
                    config={"tags": [TAG_NOSTREAM]},
                ),
                timeout=JUDGE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("验证判官超时，按 degraded 记录")
            return "degraded", [], {}, {}, "", True
        except Exception:  # noqa: BLE001 - 判官失败不影响主流程
            logger.warning("验证判官调用失败，按 degraded 记录", exc_info=True)
            return "degraded", [], {}, {}, "", True

        payload = extract_json(message_text(response))
        if payload is None:
            return "degraded", [], {}, _usage(response), _model_name(model), True
        verdict, issues, scores, _ = normalize_payload(payload)
        return (
            verdict,
            issues,
            scores,
            _usage(response),
            _model_name(model),
            False,
        )

    async def _delegate(
        self,
        request: VerificationRequest,
        issues: list[VerificationIssue],
    ) -> tuple[list[VerificationIssue], dict[str, int], bool]:
        """L2：用验证子 Agent 的提示词做一次深评。

        自动链路直接复用 YAML 中的系统提示，避免为一次验证构建完整子图；
        需要完整子 Agent 能力时，主 Agent 仍可通过 ``task`` 手动委派。
        """
        from agent.config import get_judge_model
        from agent.subagents.configs import config_path
        from agent.subagents.loader import read_subagent_config

        budget.note_delegation(request.context)
        tokens: dict[str, int] = {}
        try:
            model = get_judge_model(request.user_id)
            score_config = read_subagent_config(config_path("verifier_score.yaml"))
            response = await self._invoke_role(
                model,
                str(score_config["system_prompt"]),
                build_judge_prompt(request),
            )
        except Exception:  # noqa: BLE001 - L2 失败回退 L1 结论
            logger.warning("验证子 Agent 深评失败，保留 L1 结论", exc_info=True)
            return issues, tokens, False

        tokens = _merge_tokens(tokens, _usage(response))
        payload = extract_json(message_text(response))
        if payload is None:
            return issues, tokens, False
        verdict, deeper_issues, _, _ = normalize_payload(payload)
        if deeper_issues:
            issues = deeper_issues
        if verdict != "fail":
            return issues, tokens, True

        try:
            critic_config = read_subagent_config(config_path("verifier_critic.yaml"))
            critic = await self._invoke_role(
                model,
                str(critic_config["system_prompt"]),
                build_critic_prompt(request, issues),
            )
            tokens = _merge_tokens(tokens, _usage(critic))
            critique = message_text(critic)
            if critique:
                for issue in issues:
                    if issue.severity == "blocking" and not issue.suggestion:
                        issue.suggestion = critique[:500]
                        break
        except Exception:  # noqa: BLE001 - 评分已成功，批判失败不降级整体结论
            logger.warning("验证批判子 Agent 失败，保留评分结论", exc_info=True)
        return issues, tokens, True

    @staticmethod
    async def _invoke_role(model: Any, system_prompt: str, user_prompt: str) -> Any:
        return await asyncio.wait_for(
            model.ainvoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                config={"tags": [TAG_NOSTREAM]},
            ),
            timeout=JUDGE_TIMEOUT * 3,
        )

    # ---------- 异步 Schema / 数值验证 ----------

    @staticmethod
    async def _run_async_verifiers(
        request: VerificationRequest,
    ) -> list[VerificationIssue]:
        if request.context is None:
            return []
        try:
            from agent.evolution.verifiers import build_default_verifiers

            issues: list[VerificationIssue] = []
            for verifier in build_default_verifiers(request.context):
                try:
                    result = await verifier.verify(request.answer, request.context)
                except Exception:  # noqa: BLE001
                    continue
                if not result.passed:
                    issues.append(
                        VerificationIssue(
                            dimension="usability",
                            severity="blocking" if result.fatal else "major",
                            message=result.detail or f"{verifier.name} 未通过",
                            evidence_ref=f"verifier:{verifier.name}",
                            suggestion="按断言要求修正输出格式或数值",
                        )
                    )
            return issues
        except Exception:  # noqa: BLE001
            return []

    # ---------- 落库与信号 ----------

    async def _write_event(
        self, request: VerificationRequest, record: VerificationRecord
    ) -> None:
        if request.store is None or not request.user_id or not request.thread_id:
            return
        try:
            await append_trajectory_event(
                request.store,
                user_id=request.user_id,
                thread_id=request.thread_id,
                event=record.to_event(),
            )
        except Exception:  # noqa: BLE001 - 轨迹写入失败只告警
            logger.warning("写入验证轨迹失败", exc_info=True)

    @staticmethod
    def _apply_signals(context: Any, record: VerificationRecord) -> None:
        if context is None:
            return
        try:
            if record.verdict == "degraded":
                context.verification_degraded = True
            if record.blocking_count:
                context.verification_blocking = (
                    int(getattr(context, "verification_blocking", 0) or 0)
                    + record.blocking_count
                )
            existing = json.loads(
                str(getattr(context, "verification_issues_json", "") or "[]")
            )
            if not isinstance(existing, list):
                existing = []
            existing.extend(issue.to_dict() for issue in record.issues)
            context.verification_issues_json = json.dumps(
                existing[-50:], ensure_ascii=False
            )
        except Exception:  # noqa: BLE001 - 信号写入失败不影响主流程
            logger.debug("写入验证信号失败", exc_info=True)

    async def _record_reward(
        self, request: VerificationRequest, record: VerificationRecord
    ) -> None:
        """把阻断问题写入奖励账本；同一验证只记一次（event_id 唯一）。"""
        if (
            request.store is None
            or not request.user_id
            or not request.thread_id
            or not record.blocking_count
        ):
            return
        try:
            from agent.evolution.reward import append_reward

            tenant_id = str(
                getattr(request.context, "tenant_id", "") or "default"
            )
            first = next(
                issue for issue in record.issues if issue.severity == "blocking"
            )
            await append_reward(
                request.store,
                user_id=request.user_id,
                thread_id=request.thread_id,
                tenant_id=tenant_id,
                execution_id=request.run_id or request.thread_id,
                event_id=record.verification_id,
                source="verification",
                value=-0.1,
                category="verification_issue",
                confidence=0.7,
                evidence=(
                    f"{record.point}/{record.level}: {first.message}"
                ),
                delayed=True,
            )
        except Exception:  # noqa: BLE001 - 奖励写入失败不影响主流程
            logger.debug("写入验证奖励失败", exc_info=True)


def _merge_tokens(*values: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for value in values:
        for key, amount in (value or {}).items():
            merged[key] = merged.get(key, 0) + int(amount or 0)
    return merged


_RUNNER: VerificationRunner | None = None


def get_verification_runner() -> VerificationRunner:
    """进程内单例；runner 本身无状态，预算在 context 上。"""
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = VerificationRunner()
    return _RUNNER


__all__ = [
    "VerificationRunner",
    "get_verification_runner",
    "point_enabled",
]
