"""⑤ 流程路由器：输入当前任务，输出选哪条执行计划 / 走通用循环 / 中途接管。

召回：v1 走关键词打分（embedding 待 pgvector 确认后启用），top-K=3。
终选：可选注入 LLM 判别器（低成本模型），默认阈值直接判定。
显式指定（请求参数 flow_id 或消息中 @流名）优先于检索。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .indexer import recall_text
from .models import ExecutionPlan, TaskFlow
from .store import TaskFlowStore

logger = logging.getLogger(__name__)

TOP_K = 3
HIT_THRESHOLD = 0.35  # 关键词召回分阈值，>= 视为候选命中

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


def _keyword_score(query: str, doc_text: str) -> float:
    """轻量关键词打分：查询与文档的字符 bigram 重合率（不依赖分词）。"""
    def bigrams(text: str) -> set[str]:
        text = re.sub(r"\s+", "", text.lower())
        return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}

    q, d = bigrams(query), bigrams(doc_text)
    if not q or not d:
        return 0.0
    return len(q & d) / len(q)


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
            decided = await self._load(flow_id)
            if decided:
                flow, plan = decided
                kind = "takeover" if entry_step else "hit"
                return RouteDecision(kind, flow, plan, entry_step, user_state)
            logger.warning("显式指定的任务流不存在或未发布：%s，降级检索", flow_id)

        candidates = await self._recall(message, user_id)
        if not candidates:
            return RouteDecision("miss")

        selected_id: Optional[str] = None
        if self._judge is not None:
            try:
                selected_id = await self._judge(message, [c["search_doc"] for c in candidates])
            except Exception:
                logger.exception("LLM 终选失败，降级为召回阈值判定")
        else:
            top = candidates[0]
            if top["score"] >= HIT_THRESHOLD:
                selected_id = top["id"]

        if selected_id is None:
            return RouteDecision("suggest", candidates=candidates)

        decided = await self._load(selected_id)
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
            score = _keyword_score(message, recall_text(doc))
            if score > 0:
                scored.append({**row, "score": score})
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored[:TOP_K]

    async def _load(self, flow_id: str) -> Optional[tuple[TaskFlow, ExecutionPlan]]:
        row = await self._store.get_flow(flow_id)
        if not row or row.get("status") != "published" or not row.get("plan"):
            return None
        return TaskFlow.model_validate(row["schema"]), ExecutionPlan.model_validate(row["plan"])
