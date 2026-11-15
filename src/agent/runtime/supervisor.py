"""运行时监督者：异常分类 + 有界重试 + 检查点续跑。

设计要点（详见 docs/core_design/运行时异常捕获与重启机制设计开发.md）：

1. **不挪线程、不挪进程**：Agent 协程与事件循环强绑定，重试仍在当前 loop 内完成。
2. **重试 = 用同一 thread_id 续跑**：第二次尝试传 ``None`` 作为输入，LangGraph
   会从最后一个 checkpoint 继续，不会重复写入用户消息。
3. **三重刹车**：错误分类（确定性异常不重试）、尝试次数、无进展探针
   （连续两次重试后 checkpoint 的消息数与待执行节点都没变，判定毒丸）。
4. **向用户只报告"正在自愈"**：重试过程产出 ``RuntimeNotice``，由接入方转成
   前端轨迹提示；堆栈只进日志。
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from .errors import ErrorCategory, ErrorInfo, classify

logger = logging.getLogger(__name__)

# 与 api/chat.py 的流式订阅保持一致；变更时两处必须同步。
STREAM_MODES = ["messages", "values", "custom"]
# 读取 checkpoint 的超时：探测失败就不重试，避免在 DB 故障时雪上加霜。
PROBE_TIMEOUT_S = 10.0
# 退避上限，避免长任务被无意义地拖住。
BACKOFF_MAX_MS = 8_000


@dataclass(frozen=True)
class RuntimeNotice:
    """面向用户的运行时提示；不包含堆栈与内部类名。"""

    code: str
    message: str
    level: str = "warn"
    attempt: int = 1
    max_attempts: int = 1
    retryable: bool = True

    def to_event(self, thread_id: str) -> dict[str, Any]:
        return {
            "type": "runtime_notice",
            "thread_id": thread_id,
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class _Probe:
    """用于判断"重试是否真的往前走了"的轻量快照。"""

    message_count: int
    next_nodes: tuple[str, ...]


class RunSupervisor:
    """包装 ``agent.astream``，在瞬时异常时按预算重试。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_attempts: int = 3,
        backoff_ms: int = 1000,
        no_progress_limit: int = 2,
    ) -> None:
        self._enabled = enabled
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_ms = max(0, int(backoff_ms))
        self._no_progress_limit = max(1, int(no_progress_limit))
        self._stream: Any = None

    # ----------------------------------------------------------
    # 对外入口
    # ----------------------------------------------------------

    async def stream(
        self,
        agent: Any,
        agent_input: Any,
        *,
        config: dict[str, Any],
        context: Any,
        run_id: str = "",
        thread_id: str = "",
        resumable: bool = True,
        has_partial_answer: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[Any]:
        """产出原始流事件，或在重试前插入 ``RuntimeNotice``。"""
        attempt = 1
        baseline: Optional[_Probe] = None
        stalled = 0
        partial = has_partial_answer or (lambda: False)

        logger.info(
            "event=agent.run.start run_id=%s thread_id=%s attempt=%s",
            run_id,
            thread_id,
            attempt,
        )
        while True:
            stream = agent.astream(
                agent_input if attempt == 1 else None,
                config=config,
                context=context,
                stream_mode=STREAM_MODES,
                subgraphs=True,
                version="v2",
            )
            self._stream = stream
            try:
                async for chunk in stream:
                    yield chunk
            except Exception as exc:  # noqa: BLE001 - 是否重试由分类结果决定
                info = classify(exc)
                if info.category is ErrorCategory.CANCELLED:
                    # HITL 暂停 / 取消：不是故障，保持原样冒泡。
                    logger.info(
                        "event=agent.run.cancelled run_id=%s attempt=%s error_class=%s",
                        run_id,
                        attempt,
                        info.error_class,
                    )
                    raise
                await self._close_stream()

                reason = self._retry_blocker(
                    info=info,
                    attempt=attempt,
                    resumable=resumable,
                )
                probe = await self._probe(agent, config) if reason is None else None
                if reason is None and probe is None:
                    reason = "无法读取 checkpoint 状态"
                if reason is None and probe is not None and not probe.next_nodes and partial():
                    # 没有待执行节点说明图其实已经走完，续跑拿不回正文。
                    reason = "图已无待执行节点"
                if reason is None and probe is not None and baseline is not None:
                    stalled = stalled + 1 if probe == baseline else 0
                    if stalled >= self._no_progress_limit:
                        reason = "重试无进展"
                if reason is not None:
                    logger.error(
                        "event=agent.run.failed run_id=%s thread_id=%s attempt=%s "
                        "category=%s error_class=%s fingerprint=%s reason=%s",
                        run_id,
                        thread_id,
                        attempt,
                        info.category.value,
                        info.error_class,
                        info.fingerprint,
                        reason,
                        exc_info=True,
                    )
                    raise

                baseline = probe
                attempt += 1
                backoff = self._backoff(attempt - 1)
                logger.warning(
                    "event=agent.run.retry run_id=%s thread_id=%s attempt=%s/%s "
                    "error_class=%s fingerprint=%s backoff_ms=%s",
                    run_id,
                    thread_id,
                    attempt,
                    self._max_attempts,
                    info.error_class,
                    info.fingerprint,
                    backoff,
                )
                await asyncio.sleep(backoff / 1000)
                yield RuntimeNotice(
                    code="INTERNAL_RETRY",
                    message=(
                        f"内部异常，执行重试机制（第 {attempt}/{self._max_attempts} 次）…"
                    ),
                    attempt=attempt,
                    max_attempts=self._max_attempts,
                )
            else:
                if attempt > 1:
                    logger.warning(
                        "event=agent.run.recovered run_id=%s thread_id=%s attempts=%s",
                        run_id,
                        thread_id,
                        attempt,
                    )
                return
            finally:
                await self._close_stream()

    async def aclose(self) -> None:
        """调用方提前结束时也要释放底层流。"""
        await self._close_stream()

    # ----------------------------------------------------------
    # 内部
    # ----------------------------------------------------------

    def _retry_blocker(
        self,
        *,
        info: ErrorInfo,
        attempt: int,
        resumable: bool,
    ) -> str | None:
        """返回不可重试的原因；None 表示允许继续重试。"""
        if not self._enabled:
            return "重试已关闭"
        if not info.retryable:
            return f"异常类别不可重试：{info.category.value}"
        if not resumable:
            return "当前请求不支持续跑（恢复/重新生成）"
        if attempt >= self._max_attempts:
            return "重试次数已耗尽"
        return None

    def _backoff(self, index: int) -> int:
        """指数退避 + ±20% 抖动，避免同一时刻的重试风暴。"""
        base = self._backoff_ms * (2 ** max(0, index - 1))
        jitter = base * random.uniform(-0.2, 0.2)
        return int(max(0, min(BACKOFF_MAX_MS, base + jitter)))

    async def _probe(self, agent: Any, config: dict[str, Any]) -> Optional[_Probe]:
        """读取 checkpoint 摘要；任何失败都视为"无法判断"，不重试。"""
        get_state = getattr(agent, "aget_state", None)
        if not callable(get_state):
            return None
        try:
            state = await asyncio.wait_for(get_state(config), timeout=PROBE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - 探测失败不影响主流程
            logger.warning("event=agent.run.probe_failed", exc_info=True)
            return None
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") or []
        next_nodes = tuple(str(node) for node in (getattr(state, "next", None) or ()))
        return _Probe(message_count=len(messages), next_nodes=next_nodes)

    async def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        close = getattr(stream, "aclose", None)
        if not callable(close):
            return
        try:
            await close()
        except Exception:  # noqa: BLE001 - 关闭旧流失败不能覆盖原始异常
            logger.debug("关闭旧流失败", exc_info=True)


__all__ = ["RunSupervisor", "RuntimeNotice"]
