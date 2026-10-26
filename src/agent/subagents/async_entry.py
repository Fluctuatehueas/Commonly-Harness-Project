"""为 Agent Protocol Server 构造已注册的异步子 Agent 图。

一个 Agent Protocol 图入口（``async_subagent``）承载全部已注册子 Agent：
调用方通过 ``context["subagent_name"]`` 指定具体子 Agent，并通过
``context["sandbox_id"]`` 复用主 Agent 的沙箱。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend
from langgraph_sdk.runtime import ServerRuntime
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.sync import SandboxSync

from agent.backends.open_sandbox import OpenSandboxBackend
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.skill_sync import SandboxSkillSynchronizer
from agent.config import (
    ASYNC_SUBAGENT_MODEL_RUN_LIMIT,
    ASYNC_SUBAGENT_TOOL_RUN_LIMIT,
    OPEN_SANDBOX_API_KEY,
    OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS,
    OPEN_SANDBOX_HOST,
    OPEN_SANDBOX_PORT,
    OPEN_SANDBOX_USE_SERVER_PROXY,
    SKILLS_ROOT,
    get_main_model,
    get_summary_model,
)
from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.subagents.async_registry import (
    ASYNC_SUBAGENT_GRAPH_ID,
    get_async_subagent_registration,
)
from agent.subagents.loader import aload_subagent


# 配置加载涉及异步工具（例如 MCP），在图首次构建时缓存，避免每个任务重复加载。
_SUBAGENT_CONFIGS: dict[str, dict[str, Any]] = {}


async def _load_subagent_config(name: str) -> dict[str, Any]:
    """加载指定异步子 Agent 的 YAML 配置、MCP 工具和独立工具。"""
    registration = get_async_subagent_registration(name)
    return await aload_subagent(
        registration.config_path,
        await registration.tool_loader(),
    )


async def _get_subagent_config(name: str) -> dict[str, Any]:
    """按名称读取并缓存子 Agent 配置。"""
    if name not in _SUBAGENT_CONFIGS:
        _SUBAGENT_CONFIGS[name] = await _load_subagent_config(name)
    return _SUBAGENT_CONFIGS[name]


def build_async_subagent_graph(
    name: str,
    subagent_config: dict[str, Any],
    sandbox_backend: SandboxBackendProxy,
) -> Any:
    """用共享沙箱后端构建一个异步子 Agent 图。"""
    backend = CompositeBackend(default=sandbox_backend, routes={})
    skill_synchronizer = SandboxSkillSynchronizer(
        SKILLS_ROOT,
        Path(__file__).parent.parent / "memory" / "AGENTS.md",
    )
    skills = subagent_config.get("skills", [])
    return create_deep_agent(
        model=get_main_model(),
        system_prompt=subagent_config["system_prompt"],
        tools=subagent_config["tools"],
        skills=skills,
        backend=backend,
        middleware=[
            # 远程图共享用户沙箱，必须先同步技能再读取 YAML 指定的技能目录。
            SandboxSkillsMiddleware(
                backend=backend,
                sources=skills,
                synchronizer=skill_synchronizer,
            ),
            *build_agent_protection_middleware(
                backend=backend,
                summary_model=get_summary_model(),
                model_run_limit=ASYNC_SUBAGENT_MODEL_RUN_LIMIT,
                tool_run_limit=ASYNC_SUBAGENT_TOOL_RUN_LIMIT,
            ),
        ],
        interrupt_on=subagent_config.get("interrupt_on"),
        checkpointer=None,
        name=ASYNC_SUBAGENT_GRAPH_ID,
    )


def _context_value(context: Any, key: str) -> Any:
    """兼容 Agent Protocol 传入的字典或对象形式的 context。"""
    if isinstance(context, dict):
        return context.get(key)
    return getattr(context, key, None) if context is not None else None


@asynccontextmanager
async def async_subagent_graph(runtime: ServerRuntime) -> AsyncIterator[Any]:
    """只在执行上下文连接沙箱，并在图生命周期结束后释放客户端。"""
    proxy = SandboxBackendProxy()
    try:
        execution = runtime.execution_runtime
        context = execution.context if execution is not None else None
        subagent_name = _context_value(context, "subagent_name")
        if not subagent_name:
            raise RuntimeError("异步任务缺少 subagent_name 上下文")

        subagent_config = await _get_subagent_config(str(subagent_name))

        if execution is not None:
            sandbox_id = _context_value(context, "sandbox_id")
            if not sandbox_id:
                raise RuntimeError("异步任务缺少 sandbox_id 上下文")
            if not OPEN_SANDBOX_API_KEY:
                raise RuntimeError(
                    "未配置 OPEN_SANDBOX_API_KEY，异步子 Agent 无法连接沙箱"
                )
            connection = ConnectionConfigSync(
                api_key=OPEN_SANDBOX_API_KEY,
                domain=f"{OPEN_SANDBOX_HOST}:{OPEN_SANDBOX_PORT}",
                use_server_proxy=OPEN_SANDBOX_USE_SERVER_PROXY,
            )
            task = asyncio.create_task(
                asyncio.to_thread(
                    SandboxSync.connect, str(sandbox_id), connection_config=connection,
                )
            )
            try:
                sandbox = await asyncio.shield(task)
            except asyncio.CancelledError:
                # 同步 SDK 可能在取消后仍完成；必须接住结果并关闭已创建的客户端。
                try:
                    sandbox = await task
                except Exception:
                    pass
                else:
                    await asyncio.to_thread(sandbox.close)
                raise
            try:
                backend = OpenSandboxBackend(
                    sandbox, OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS,
                )
            except BaseException:
                await asyncio.to_thread(sandbox.close)
                raise
            proxy.replace_backend(backend)

        yield build_async_subagent_graph(str(subagent_name), subagent_config, proxy)
    finally:
        await asyncio.to_thread(proxy.close)
