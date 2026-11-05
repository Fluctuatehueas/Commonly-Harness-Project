"""压缩锚点：提取必须存活的事实，并在压缩后校验。

v1 只使用确定性规则（正则 + 关键词），不额外调用模型：
- 用户目标与任务相关用户消息逐字保留；
- 文件路径、命令、错误码、标识符；
- 调用方传入的控制状态（HITL 回复 / task_id 等）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 路径、命令与标识符的保守匹配，宁可漏判也不要把普通词当锚点。
_PATH_RE = re.compile(r"(/[\w.\-]+(?:/[\w.\-]+)+|[A-Za-z]:\\[\w.\-\\]+)")
_ID_RE = re.compile(
    r"(?:task_id|approval_id|session_id)\s*[=:]\s*[\w.-]+"
    r"|\b[0-9a-f]{32}\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(r"(?:E\d{3,}|HTTP\s?\d{3}|4\d{2}|5\d{2})")
_CMD_RE = re.compile(r"`([^`\n]{2,120})`")

# 用户补充里常见的“与任务相关”标志词。
_RELEVANT_MARKERS = (
    "必须",
    "不要",
    "不能",
    "只能",
    "改成",
    "改为",
    "修改",
    "要求",
    "约束",
    "继续",
    "上面",
    "刚才",
    "这份",
    "这个",
    "帮我",
    "请看",
)

MAX_ANCHORS = 40
MAX_USER_TEXT = 400


@dataclass(frozen=True)
class Anchor:
    """一条锁定事实。"""

    kind: str
    value: str


def message_text(message: Any) -> str:
    """把消息内容归一化为纯文本。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _human_messages(messages: list[Any]) -> list[str]:
    return [
        message_text(message)
        for message in messages
        if getattr(message, "type", None) in {"human", "user"} and message_text(message)
    ]


def _keywords(text: str) -> set[str]:
    """粗粒度关键词：中文双字片段 + 英文/数字词。"""
    words = {word.lower() for word in re.findall(r"[A-Za-z0-9_]{3,}", text)}
    chinese = re.sub(r"[^\u4e00-\u9fa5]", "", text)
    words.update(chinese[index : index + 2] for index in range(len(chinese) - 1))
    return words


def _is_relevant(text: str, goal: str) -> bool:
    """判断一条历史用户消息是否与当前任务相关（保守策略）。"""
    if any(marker in text for marker in _RELEVANT_MARKERS):
        return True
    goal_words = _keywords(goal)
    if not goal_words:
        return False
    hits = sum(1 for word in goal_words if word and word in text.lower())
    return hits >= 2


def _push(anchors: list[Anchor], kind: str, value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    if any(item.value == cleaned for item in anchors):
        return
    anchors.append(Anchor(kind=kind, value=cleaned))


def extract_anchors(
    messages: list[Any],
    *,
    extra: list[str] | None = None,
    limit: int = MAX_ANCHORS,
) -> list[Anchor]:
    """按优先级提取锚点：目标 → 任务相关用户消息 → 路径/命令 → 标识符 → 控制状态。"""
    anchors: list[Anchor] = []
    humans = _human_messages(messages)
    goal = humans[-1][:MAX_USER_TEXT] if humans else ""

    if goal:
        _push(anchors, "goal", goal)
        for text in humans[:-1]:
            if _is_relevant(text, goal):
                _push(anchors, "user_message", text[:MAX_USER_TEXT])

    joined = "\n".join(message_text(message) for message in messages)
    for match in _PATH_RE.findall(joined):
        _push(anchors, "path", match)
    for match in _CMD_RE.findall(joined):
        _push(anchors, "command", f"`{match}`")
    for match in _ERROR_RE.findall(joined):
        _push(anchors, "error", match)
    for match in _ID_RE.findall(joined):
        _push(anchors, "identifier", match)

    for value in extra or []:
        _push(anchors, "state", value)

    return anchors[:limit]


def render_anchor_block(anchors: list[Anchor]) -> str:
    """把锚点渲染成摘要中的锁定事实段落；空列表返回空串。"""
    if not anchors:
        return ""
    labels = {
        "goal": "用户目标",
        "user_message": "任务相关用户消息",
        "path": "文件路径",
        "command": "命令",
        "error": "错误码",
        "identifier": "标识符",
        "state": "控制状态",
    }
    lines = ["## 锁定事实（必须原样保留）"]
    for item in anchors:
        lines.append(f"- {labels.get(item.kind, item.kind)}：{item.value}")
    return "\n".join(lines)


def anchor_fidelity(summary: str, anchors: list[Anchor]) -> tuple[int, int]:
    """返回（保留数, 总数）。"""
    if not anchors:
        return 0, 0
    kept = sum(1 for anchor in anchors if anchor.value in summary)
    return kept, len(anchors)
