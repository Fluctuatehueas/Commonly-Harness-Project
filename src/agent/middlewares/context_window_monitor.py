"""每轮对话上下文窗口与 Token 消耗监控中间件。

只读：不修改 state，只写结构化日志。阈值告警用于可观测性（日志 / 外部
监控 / 前端展示），本身不会改变 Agent 行为；真正需要干预时由压缩中间件
``build_agent_protection_middleware`` 负责。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.runtime import Runtime

from agent.config import CONTEXT_ALERT_THRESHOLD, MODEL_CONTEXT_LIMIT


logger = logging.getLogger(__name__)


class ContextWindowMonitorMiddleWare(AgentMiddleware):
    """在 before_agent / after_agent 采集上下文 Token 占用。"""

    def __init__(
        self,
        alert_threshold: float = CONTEXT_ALERT_THRESHOLD,
        context_limit: int = MODEL_CONTEXT_LIMIT,
    ) -> None:
        super().__init__()
        self.alert_threshold = float(alert_threshold)
        self.context_limit = int(context_limit or 0)

    # ---------- 同步版本 ----------

    def before_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """Agent 启动前采集一次上下文占用。"""
        self._check_and_report(state, phase="before_agent")
        return None

    def after_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """Agent 结束后再采集一次，观察本轮对话对上下文的消耗。"""
        self._check_and_report(state, phase="after_agent")
        return None

    # ---------- 异步版本（托管版 DeepAgent 强制走异步） ----------

    async def abefore_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        self._check_and_report(state, phase="abefore_agent")
        return None

    async def aafter_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        self._check_and_report(state, phase="aafter_agent")
        return None

    def _check_and_report(self, state: dict[str, Any], phase: str) -> None:
        """计算并记录当前消息列表的 Token 占用，不修改 state。"""
        messages = (state or {}).get("messages", []) or []
        if not messages:
            return
        try:
            used = int(count_tokens_approximately(messages=list(messages)))
        except Exception:
            logger.warning("上下文 Token 估算失败，跳过本次监控", exc_info=True)
            return

        limit = self.context_limit
        if limit <= 0:
            logger.info("[ContextMonitor:%s] used_tokens=%d context_limit=unknown", phase, used)
            return

        pct = used / limit
        bar = self._make_bar(pct)
        logger.info(
            "[ContextMonitor:%s] %s used=%d / limit=%d (%.1f%%)",
            phase,
            bar,
            used,
            limit,
            pct * 100,
        )
        if pct >= self.alert_threshold:
            logger.warning(
                "[ContextMonitor:%s] 上下文窗口占用 %.1f%%，已超过阈值 %.0f%%，"
                "建议压缩历史或拆分子任务",
                phase,
                pct * 100,
                self.alert_threshold * 100,
            )

    @staticmethod
    def _make_bar(pct: float, width: int = 20) -> str:
        """生成占用进度条，异常比例也不会画出负宽度。"""
        ratio = min(max(pct, 0.0), 1.0)
        filled = int(ratio * width)
        return "[" + "█" * filled + "░" * (width - filled) + "]"
