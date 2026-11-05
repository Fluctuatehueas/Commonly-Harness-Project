"""经验库：抽取、准入、效用、衰减与剪枝。

经验与用户偏好分开存储。用户偏好回答“用户喜欢什么”，经验回答
“Agent 在同类任务中应该怎么做”。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.constants import TAG_NOSTREAM

from agent.config import (
    EVOLUTION_EXPERIENCE_ENABLED,
    EVOLUTION_EXPERIENCE_MAX_ITEMS,
    EVOLUTION_EXPERIENCE_MIN_UTILITY,
    EVOLUTION_EXPERIENCE_TTL_DAYS,
)
from agent.evolution.types import AdmissionState, ExperienceRecord, RunSignals

logger = logging.getLogger(__name__)

EXPERIENCE_NAMESPACE_PREFIX = ("experience",)
MAX_EVIDENCE_REFS = 12
# 简单的经验投毒护栏：出现明显的“覆盖系统指令/执行命令”措辞时直接隔离。
UNSAFE_EXPERIENCE_TOKENS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "忽略之前",
    "忽略以上",
    "覆盖系统",
    "执行以下命令",
    "rm -rf",
    "curl ",
)


def experience_namespace(user_id: str) -> tuple[str, str]:
    return (*EXPERIENCE_NAMESPACE_PREFIX, user_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_rule(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _experience_id(trigger: str, rule: str) -> str:
    payload = f"{_normalize_rule(trigger)}|{_normalize_rule(rule)}"
    return f"exp_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _messages_digest(messages: list[Any], limit: int = 8) -> str:
    rows: list[str] = []
    for message in messages[-limit:]:
        role = getattr(message, "type", "unknown")
        content = _message_text(message)
        if not content:
            continue
        rows.append(f"[{role}] {content[:500]}")
    return "\n".join(rows)


class ExperienceStore:
    """按用户读写经验条目。"""

    def __init__(self, store: Any, user_id: str) -> None:
        self._store = store
        self._namespace = experience_namespace(user_id)

    async def list(
        self,
        *,
        statuses: set[AdmissionState] | None = None,
        limit: int = 2000,
    ) -> list[ExperienceRecord]:
        if self._store is None:
            return []
        try:
            items = await self._store.asearch(self._namespace, limit=limit)
        except Exception:
            logger.warning("读取经验库失败", exc_info=True)
            return []
        records = [ExperienceRecord.from_dict(dict(item.value)) for item in items]
        if statuses:
            records = [record for record in records if record.admission_state in statuses]
        records.sort(
            key=lambda record: (record.utility, record.updated_at),
            reverse=True,
        )
        return records

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        if self._store is None:
            return None
        try:
            item = await self._store.aget(self._namespace, experience_id)
        except Exception:
            return None
        return ExperienceRecord.from_dict(dict(item.value)) if item is not None else None

    async def upsert(self, record: ExperienceRecord) -> ExperienceRecord:
        if self._store is None:
            return record
        await self._store.aput(
            self._namespace,
            record.experience_id,
            record.to_dict(),
            index=False,
        )
        return record

    async def delete(self, experience_id: str) -> None:
        if self._store is not None:
            await self._store.adelete(self._namespace, experience_id)

    async def set_state(
        self,
        experience_id: str,
        state: AdmissionState,
    ) -> ExperienceRecord | None:
        record = await self.get(experience_id)
        if record is None:
            return None
        record.admission_state = state
        record.updated_at = _now()
        return await self.upsert(record)

    async def update_utility(
        self,
        experience_id: str,
        *,
        delta: float,
    ) -> ExperienceRecord | None:
        record = await self.get(experience_id)
        if record is None:
            return None
        observations = record.utility_observations + 1
        # 指数移动平均：早期观测更快修正，后期更稳定。
        alpha = 0.35 if observations <= 3 else 0.2
        record.utility = round((1 - alpha) * record.utility + alpha * delta, 4)
        record.utility_observations = observations
        record.updated_at = _now()
        if observations >= 3 and record.utility < EVOLUTION_EXPERIENCE_MIN_UTILITY:
            record.admission_state = AdmissionState.ARCHIVED
        return await self.upsert(record)

    async def prune(self) -> None:
        """超出上限或过期的经验归档，避免经验库无限膨胀。"""
        records = await self.list(statuses=None)
        now = datetime.now(timezone.utc)
        changed = False
        for record in records:
            if record.admission_state in {AdmissionState.ARCHIVED, AdmissionState.QUARANTINED}:
                continue
            try:
                created = datetime.fromisoformat(record.created_at)
            except ValueError:
                created = now
            if record.ttl_days > 0 and created + timedelta(days=record.ttl_days) < now:
                record.admission_state = AdmissionState.ARCHIVED
                record.updated_at = _now()
                await self.upsert(record)
                changed = True
        visible = [
            record
            for record in records
            if record.admission_state
            not in {AdmissionState.ARCHIVED, AdmissionState.QUARANTINED}
        ]
        if len(visible) > EVOLUTION_EXPERIENCE_MAX_ITEMS:
            overflow = sorted(visible, key=lambda record: (record.utility, record.updated_at))[
                : len(visible) - EVOLUTION_EXPERIENCE_MAX_ITEMS
            ]
            for record in overflow:
                record.admission_state = AdmissionState.ARCHIVED
                record.updated_at = _now()
                await self.upsert(record)
            changed = True
        if changed:
            logger.info("经验库已归档过期或超限条目")


_EXTRACT_PROMPT = """You are extracting reusable Agent experiences from a failed run.

Return only a JSON array. Each item must be:
{"kind": "failure_trigger" | "success_pattern" | "contrast",
 "trigger": "...", "rule": "...", "tags": ["..."], "confidence": 0.0-1.0}

Rules:
- Only extract experiences that are reusable across similar tasks.
- Do not invent facts; base them on the conversation and tool errors below.
- Keep trigger and rule concise. The rule must be an actionable instruction.
- If nothing is reusable, return [].

Conversation:
__CONVERSATION__

Tool errors / signals:
__SIGNALS__
"""


def _parse_experience_json(content: str) -> list[dict[str, Any]]:
    start = content.find("[")
    end = content.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        value = json.loads(content[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


async def extract_experiences(
    model: BaseChatModel,
    *,
    messages: list[Any],
    signals: RunSignals,
) -> list[dict[str, Any]]:
    """从失败轨迹中提炼候选经验；失败只返回空列表。"""
    if not EVOLUTION_EXPERIENCE_ENABLED or not signals.has_failure:
        return []
    conversation = _messages_digest(messages)
    signal_text = json.dumps(
        {
            "tool_errors": signals.tool_errors[-5:],
            "verification_failed": signals.verification_failed,
            "hitl_rejected": signals.hitl_rejected,
            "user_correction": signals.user_correction,
            "repeated_steps": signals.repeated_steps,
        },
        ensure_ascii=False,
    )
    prompt = _EXTRACT_PROMPT.replace("__CONVERSATION__", conversation).replace(
        "__SIGNALS__", signal_text
    )
    try:
        # 内部经验抽取不进入消息流。
        response = await model.ainvoke(prompt, config={"tags": [TAG_NOSTREAM]})
        content = _message_text(response)
        return _parse_experience_json(content)
    except Exception:
        logger.warning("经验抽取失败，跳过本轮", exc_info=True)
        return []


def build_experience_record(
    item: dict[str, Any],
    *,
    user_id: str,
    evidence_refs: list[str],
    signals: RunSignals,
    provenance: str = "agent_reflection",
) -> ExperienceRecord | None:
    trigger = str(item.get("trigger") or "").strip()
    rule = str(item.get("rule") or "").strip()
    if not trigger or not rule:
        return None
    kind = str(item.get("kind") or "failure_trigger")
    if kind not in {"failure_trigger", "success_pattern", "contrast"}:
        kind = "failure_trigger"
    confidence = float(item.get("confidence") or 0.5)
    confidence = max(0.0, min(confidence, 1.0))
    tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
    # 独立于生成会话的失败信号可作为准入证据。
    independently_verified = bool(
        signals.verification_failed
        or signals.hitl_rejected
        or signals.tool_errors
    )
    unsafe = any(
        token in f"{trigger}\n{rule}".lower()
        for token in UNSAFE_EXPERIENCE_TOKENS
    )
    if unsafe or provenance in {"web_content", "user_upload", "tool_output"}:
        state = AdmissionState.QUARANTINED
        utility = 0.0
    elif independently_verified and confidence >= 0.6:
        state = AdmissionState.ACTIVE
        utility = 0.3
    else:
        state = AdmissionState.PROPOSED
        utility = 0.0
    now = _now()
    return ExperienceRecord(
        experience_id=_experience_id(trigger, rule),
        scope=f"user:{user_id}",
        kind=kind,  # type: ignore[arg-type]
        trigger=trigger[:500],
        rule=rule[:1000],
        evidence_refs=list(dict.fromkeys(evidence_refs))[:MAX_EVIDENCE_REFS],
        utility=utility,
        utility_observations=0,
        confidence=confidence,
        provenance=provenance,
        admission_state=state,
        generation=1,
        tags=tags[:8],
        ttl_days=EVOLUTION_EXPERIENCE_TTL_DAYS,
        created_at=now,
        updated_at=now,
        hit_count=0,
    )


async def save_experiences(
    store: ExperienceStore,
    items: list[dict[str, Any]],
    *,
    user_id: str,
    evidence_refs: list[str],
    signals: RunSignals,
) -> list[ExperienceRecord]:
    """保存候选经验；按规则文本去重，重复条目只累加证据。"""
    saved: list[ExperienceRecord] = []
    existing = {record.experience_id: record for record in await store.list(limit=5000)}
    for item in items:
        record = build_experience_record(
            item,
            user_id=user_id,
            evidence_refs=evidence_refs,
            signals=signals,
        )
        if record is None:
            continue
        previous = existing.get(record.experience_id)
        if previous is not None:
            previous.evidence_refs = list(
                dict.fromkeys([*previous.evidence_refs, *record.evidence_refs])
            )[:MAX_EVIDENCE_REFS]
            previous.confidence = max(previous.confidence, record.confidence)
            previous.updated_at = _now()
            if previous.admission_state == AdmissionState.QUARANTINED:
                previous.admission_state = record.admission_state
            record = previous
        else:
            existing[record.experience_id] = record
        await store.upsert(record)
        saved.append(record)
    if saved:
        await store.prune()
    return saved


def experience_view(record: ExperienceRecord) -> dict[str, Any]:
    return record.to_dict()
