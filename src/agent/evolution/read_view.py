"""轨迹读取时的 ATDP 视图补齐。

历史轨迹在写入时只保留 ``r_t`` 占位与 ``y_t.trust=unknown``，原因有两个：
奖励在写入时还没产生，用户反馈在写入后才会发生。本模块在读取轨迹时把
奖励账本与反馈账本聚合进来，得到一个"到目前为止"的视图。

约束：

- 只修改 ``list_trajectory_events`` 返回的内存副本，不回写 Store；
- 任何读取失败都退回原始事件，不阻断轨迹页；
- 语义保持保守：只有拿到直接证据时才改写 ``trust``，否则维持 ``unknown``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.config import EVOLUTION_ENABLED

logger = logging.getLogger(__name__)

TRUST_ACCEPTED = "accepted"
TRUST_REJECTED = "rejected"
TRUST_CORRECTED = "corrected"
TRUST_UNKNOWN = "unknown"

# 反馈发生在回答之后，允许的最大回溯窗口；超出窗口不猜测归属。
FEEDBACK_MATCH_WINDOW = timedelta(minutes=30)

_FEEDBACK_TRUST = {
    "like": TRUST_ACCEPTED,
    "dislike": TRUST_REJECTED,
    "retry": TRUST_CORRECTED,
}


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def aggregate_rewards(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按事件标识聚合奖励账本；事件标识为空或为 run 级虚拟标识时不聚合。"""
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        event_id = str(record.get("event_id") or "")
        if not event_id or event_id.endswith(":run"):
            continue
        bucket = buckets.setdefault(
            event_id,
            {"value": 0.0, "count": 0, "sources": [], "latest_at": ""},
        )
        try:
            bucket["value"] += float(record.get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        bucket["count"] += 1
        source = str(record.get("source") or "")
        if source and source not in bucket["sources"]:
            bucket["sources"].append(source)
        created_at = str(record.get("created_at") or "")
        if created_at > str(bucket["latest_at"]):
            bucket["latest_at"] = created_at
    return buckets


def run_windows(
    events: list[dict[str, Any]],
) -> dict[str, tuple[datetime | None, datetime | None]]:
    """按 run 汇总起止时间，供反馈归属使用。"""
    windows: dict[str, list[datetime | None]] = {}
    for event in events:
        run_id = str(event.get("run_id") or "")
        if not run_id:
            continue
        start = _parse_ts(event.get("start"))
        end = _parse_ts(event.get("end"))
        current = windows.get(run_id)
        if current is None:
            windows[run_id] = [start, end]
            continue
        if start and (current[0] is None or start < current[0]):
            current[0] = start
        if end and (current[1] is None or end > current[1]):
            current[1] = end
    return {key: (value[0], value[1]) for key, value in windows.items()}


def _match_run(
    moment: datetime | None,
    windows: dict[str, tuple[datetime | None, datetime | None]],
) -> str:
    """把反馈归到"最近一个已结束且不早于反馈时间太多"的 run。"""
    if moment is None:
        return ""
    best_run = ""
    best_end: datetime | None = None
    for run_id, (start, end) in windows.items():
        if start is None or end is None:
            continue
        if moment < start or moment > end + FEEDBACK_MATCH_WINDOW:
            continue
        if best_end is None or end > best_end:
            best_end = end
            best_run = run_id
    return best_run


def resolve_run_feedback(
    events: list[dict[str, Any]],
    feedback_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """把消息级反馈归到 run，并转换为 trust 语义。"""
    windows = run_windows(events)
    resolved: dict[str, dict[str, Any]] = {}
    for record in feedback_records:
        action = str(record.get("action") or "")
        trust = _FEEDBACK_TRUST.get(action)
        if trust is None:
            continue
        moment = _parse_ts(record.get("updated_at") or record.get("created_at"))
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        run_id = str((meta or {}).get("run_id") or "")
        if run_id not in windows:
            run_id = _match_run(moment, windows)
        if not run_id:
            continue
        info = {
            "trust": trust,
            "action": action,
            "message_id": str(record.get("message_id") or ""),
            "at": moment.isoformat() if moment else "",
        }
        previous = resolved.get(run_id)
        # 以最后一条反馈为准：用户改了主意时，新的动作覆盖旧结论。
        if previous is None or str(info["at"]) >= str(previous.get("at") or ""):
            resolved[run_id] = info
    return resolved


def _terminal_event(
    events: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any] | None:
    """返回该 run 最后一条模型事件：它对应最终回答的生成。"""
    terminal: dict[str, Any] | None = None
    for event in events:
        if str(event.get("run_id") or "") != run_id:
            continue
        if str(event.get("type") or "") != "model":
            continue
        if terminal is None or str(event.get("start") or "") >= str(terminal.get("start") or ""):
            terminal = event
    return terminal


def apply_atdp_view(
    events: list[dict[str, Any]],
    rewards_by_event: dict[str, dict[str, Any]],
    run_trust: dict[str, dict[str, Any]],
) -> None:
    """把聚合结果写进事件的 ATDP 副本，不触碰 Store。"""
    for event in events:
        atdp = event.get("atdp")
        if not isinstance(atdp, dict):
            continue
        event_id = str(event.get("id") or "")
        reward = rewards_by_event.get(event_id)
        r_t = atdp.get("r_t")
        if not isinstance(r_t, dict):
            r_t = {}
            atdp["r_t"] = r_t
        if reward:
            r_t.update(
                {
                    "value": round(float(reward["value"]), 4),
                    "source": "ledger",
                    "count": int(reward["count"]),
                    "sources": list(reward["sources"]),
                    "latest_at": reward["latest_at"],
                    "resolved": True,
                    "evidence_ref": f"reward/{event_id}",
                }
            )
        else:
            r_t.setdefault("value", None)
            r_t.setdefault("source", "pending")
            r_t.setdefault("evidence_ref", "")
            r_t["resolved"] = False

    for run_id, info in run_trust.items():
        terminal = _terminal_event(events, run_id)
        if terminal is None:
            continue
        atdp = terminal.get("atdp")
        if not isinstance(atdp, dict):
            continue
        y_t = atdp.get("y_t")
        if not isinstance(y_t, dict):
            y_t = {}
            atdp["y_t"] = y_t
        y_t["trust"] = info["trust"]
        y_t["trust_source"] = "feedback"
        y_t["feedback_action"] = info["action"]
        y_t["feedback_message_id"] = info["message_id"]


def build_run_summaries(
    rewards: list[dict[str, Any]],
    run_trust: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """汇总每个 run 的奖励与反馈，供轨迹页在 run 层展示。"""
    reward_by_run: dict[str, dict[str, Any]] = {}
    for record in rewards:
        run_id = str(record.get("execution_id") or "")
        if not run_id:
            continue
        bucket = reward_by_run.setdefault(
            run_id, {"value": 0.0, "count": 0, "sources": []}
        )
        try:
            bucket["value"] += float(record.get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        bucket["count"] += 1
        source = str(record.get("source") or "")
        if source and source not in bucket["sources"]:
            bucket["sources"].append(source)

    summaries: dict[str, dict[str, Any]] = {}
    for run_id in set(reward_by_run) | set(run_trust):
        summary: dict[str, Any] = {}
        reward = reward_by_run.get(run_id)
        if reward:
            summary["r_t"] = {
                "value": round(float(reward["value"]), 4),
                "count": int(reward["count"]),
                "sources": list(reward["sources"]),
            }
        info = run_trust.get(run_id)
        if info:
            summary["y_t"] = dict(info)
        summaries[run_id] = summary
    return summaries


async def attach_atdp_views(
    store: Any,
    *,
    user_id: str,
    thread_id: str,
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """读取奖励与反馈，把视图补进事件副本；返回 run 级摘要。"""
    if not EVOLUTION_ENABLED or store is None or not events:
        return {}
    try:
        from agent.evolution.reward import list_rewards
        from agent.feedback_store import list_feedback

        reward_records = await list_rewards(
            store, user_id=user_id, thread_id=thread_id
        )
        feedback_records = await list_feedback(
            store, user_id=user_id, thread_id=thread_id
        )
    except Exception:
        logger.warning(
            "ATDP 读取视图聚合失败：user=%s thread=%s",
            user_id,
            thread_id,
            exc_info=True,
        )
        return {}

    rewards = [record.to_dict() for record in reward_records]
    rewards_by_event = aggregate_rewards(rewards)
    run_trust = resolve_run_feedback(events, feedback_records)
    apply_atdp_view(events, rewards_by_event, run_trust)
    return build_run_summaries(rewards, run_trust)
