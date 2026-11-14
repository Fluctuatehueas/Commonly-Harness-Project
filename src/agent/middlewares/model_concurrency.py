"""模型调用并发闸门。

同一前缀的并发请求，在第一个请求完成并把 KV Cache 写入之前无法互相命中。
把同时进行的模型调用限制在较小窗口内，后发的请求就能复用先完成请求写下的
前缀缓存，起到「串行预热」的效果。

默认上限 2，可用 ``HARNESS_MODEL_CONCURRENCY`` 调整；设为 1 等价于完全串行预热。
只在异步主链路生效，同步调用直接放行。
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

DEFAULT_CONCURRENCY = 2

# 每个事件循环一个信号量：FastAPI 进程内通常只有一个循环，测试可能创建多个。
_SEMAPHORES: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _limit() -> int:
    """读取并发上限；非法值回退默认值。"""
    try:
        return max(1, int(os.getenv("HARNESS_MODEL_CONCURRENCY", str(DEFAULT_CONCURRENCY))))
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_limit())
        _SEMAPHORES[loop] = semaphore
    return semaphore


@asynccontextmanager
async def model_call_slot():
    """占用一个模型调用名额，超出上限时排队等待。"""
    async with _semaphore():
        yield


class ModelConcurrencyMiddleware(AgentMiddleware):
    """限制进程内同时进行的模型调用数量。"""

    name = "model_concurrency"

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        async with model_call_slot():
            return await handler(request)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # 同步链路没有事件循环，不做并发限制。
        return handler(request)


__all__ = ["ModelConcurrencyMiddleware", "model_call_slot"]
