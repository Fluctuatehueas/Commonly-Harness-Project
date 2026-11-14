"""⑤ 流程路由器：输入当前任务，输出选哪条执行计划 / 走通用循环 / 中途接管。

召回：对流的名称、描述、触发示例与步骤摘要分别做 bigram 余弦相似度，取最大值。
      按字段分别打分而不是拼成一整段，避免长文档稀释短查询的命中分。
终选：可注入 LLM 判别器（低成本模型）对 top-K 做一次确认，默认阈值直接判定。
显式指定（请求参数 flow_id 或消息中 @流名）优先于检索。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .models import ExecutionPlan, TaskFlow
from .store import TaskFlowStore

logger = logging.getLogger(__name__)

TOP_K = 3
# bigram 余弦相似度阈值；完全相同为 1.0，覆盖大部分表达时约 0.6 以上。
HIT_THRESHOLD = 0.5
# 至少要有两个 bigram 重合，避免单个高频词直接命中。
MIN_OVERLAP = 2

# 高频功能词 bigram：出现在任何中文查询里，但不携带任务语义。
_STOP_BIGRAMS = frozenset(
    {
        "帮我", "一下", "这个", "那个", "我的", "你的", "可以", "怎么",
        "如何", "什么", "一个", "我们", "需要", "进行", "然后", "以及",
        "还有", "就是", "能否", "麻烦", "请你", "帮忙",
    }
)

# LLM 判别器签名：(用户消息, 候选检索文档列表) -> 选中的 flow_id 或 None
LLMJudge = Callable[[str, list[dict[str, Any]]], Awaitable[Optional[str]]]


@dataclass
class RouteDecision:
    """路由结果。kind: hit / takeover / miss / suggest。"""

    kind: str
    flow: Optional[TaskFlow] = None
    plan: Optional[ExecutionPlan] = None
    entry_step: Optional[str] = None  # 中途接管入口步骤
    user_state: str = ""  # 用户已完成部分的产出描述
    candidates: list[dict[str, Any]] = field(default_factory=list)  # suggest 时的候选

    @property
    def hit(self) -> bool:
        return self.kind in ("hit", "takeover")


# 中途接管意图信号：用户表示已做了一部分
_TAKEOVER_HINTS = re.compile(
    r"(已经|已)做[了完]|我做[了完]|已完成|写[了好一]?(部分|半)|从第?\s*[0-9一二三四五六七八九十]+\s*步|接着|继续(做|往下)",
)


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合（不依赖分词），去掉不携带语义的功能词。"""
    normalized = re.sub(r"\s+", "", text.lower())
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {
        normalized[index : index + 2]
        for index in range(len(normalized) - 1)
    } - _STOP_BIGRAMS


def _keyword_score(query: str, doc_text: str) -> float:
    """查询与文档的 bigram 余弦相似度：重合数 / sqrt(|q|·|d|)。"""
    q, d = _bigrams(query), _bigrams(doc_text)
    if not q or not d:
        return 0.0
    overlap = len(q & d)
    if overlap < MIN_OVERLAP:
        return 0.0
    return overlap / math.sqrt(len(q) * len(d))


def _doc_score(query: str, search_doc: dict[str, Any]) -> float:
    """按字段分别打分取最大：短触发示例不会被长描述稀释。"""
    fields = [
        search_doc.get("name", ""),
        search_doc.get("description", ""),
        search_doc.get("step_summary", ""),
        *search_doc.get("trigger_examples", []),
    ]
    return max((_keyword_score(query, text) for text in fields if text), default=0.0)


def _detect_takeover(message: str) -> bool:
    return bool(_TAKEOVER_HINTS.search(message))


class FlowRouter:
    """流程路由器。store 用于拉取候选；judge 可选注入 LLM 终选。"""

    def __init__(self, store: TaskFlowStore, judge: Optional[LLMJudge] = None) -> None:
        self._store = store
        self._judge = judge

    async def route(
        self,
        message: str,
        user_id: str,
        *,
        flow_id: Optional[str] = None,
        entry_step: Optional[str] = None,
        user_state: str = "",
    ) -> RouteDecision:
        # 显式指定优先
        if flow_id:
            decided = await self._load(flow_id, user_id)
            if decided:
                flow, plan = decided
                kind = "takeover" if entry_step else "hit"
                return RouteDecision(kind, flow, plan, entry_step, user_state)
            logger.warning(
                "显式指定的任务流不存在、未发布或已停用：%s，降级检索", flow_id
            )

        candidates = await self._recall(message, user_id)
        if not candidates:
            return RouteDecision("miss")

        selected_id: Optional[str] = None
        if self._judge is not None:
            try:
                selected_id = await self._judge(
                    message, [c["search_doc"] for c in candidates if c.get("search_doc")]
                )
            except Exception:
                logger.exception("LLM 终选失败，降级为召回阈值判定")
                top = candidates[0]
                selected_id = top["id"] if top["score"] >= HIT_THRESHOLD else None
        else:
            top = candidates[0]
            if top["score"] >= HIT_THRESHOLD:
                selected_id = top["id"]

        if selected_id is None:
            return RouteDecision("suggest", candidates=candidates)

        decided = await self._load(selected_id, user_id)
        if not decided:
            return RouteDecision("miss")
        flow, plan = decided
        if _detect_takeover(message) or entry_step:
            return RouteDecision("takeover", flow, plan, entry_step, user_state or message)
        return RouteDecision("hit", flow, plan)

    async def _recall(self, message: str, user_id: str) -> list[dict[str, Any]]:
        rows = await self._store.published_flows(user_id)
        scored = []
        for row in rows:
            doc = row.get("search_doc") or {}
            score = _doc_score(message, doc)
            if score > 0:
                scored.append({**row, "score": score})
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored[:TOP_K]

    async def _load(
        self, flow_id: str, user_id: str
    ) -> Optional[tuple[TaskFlow, ExecutionPlan]]:
        row = await self._store.get_flow(flow_id)
        if not row or row.get("status") != "published" or not row.get("plan"):
            return None
        # 停用只影响命中，不改发布状态；显式指定同样遵守停用。
        if not await self._store.is_flow_enabled(flow_id, user_id):
            return None
        return TaskFlow.model_validate(row["schema"]), ExecutionPlan.model_validate(row["plan"])
