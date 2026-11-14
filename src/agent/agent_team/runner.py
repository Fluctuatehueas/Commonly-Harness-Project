"""跨组同步委派。

本期只做 v1：调用方等待目标组组长返回结论。所有防护
（深度、委派链、可见性、超时、并发）都在这里收口。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import agent.config as agent_config
from agent.agent_team.context import TeamAgentContext
from agent.agent_team.factory import AgentContextFactory
from agent.agent_team.store import AgentGroupStore
from agent.schema import HarnessContext

logger = logging.getLogger(__name__)

_SEMAPHORE: dict[str, asyncio.Semaphore] = {}


@dataclass(frozen=True)
class TeamDeps:
    """团队工具与跨组 runner 的依赖容器，避免工具闭包抓全局。"""

    store: Any
    checkpointer: Any
    sandbox_backend: Any
    user_id: str
    username: str
    integration_server_id: str | None = None

    @property
    def group_store(self) -> AgentGroupStore:
        return AgentGroupStore(self.store, self.user_id)

    def create_config(self, thread_id: str) -> dict[str, dict[str, str]]:
        return {
            "configurable": {
                "thread_id": thread_id,
                "user_id": self.user_id,
                "username": self.username,
            }
        }


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "detail": detail}


def _last_ai_text(result: Any) -> str:
    messages = list((result or {}).get("messages", []) or [])
    for message in reversed(messages):
        if getattr(message, "type", "") == "ai":
            text = getattr(message, "text", "")
            return str(text or getattr(message, "content", "") or "")
    return ""


def build_delegation_context(
    deps: TeamDeps,
    group_id: str,
    agent_id: str,
    *,
    depth: int,
    chain: tuple[str, ...],
) -> HarnessContext:
    """跨组 run 的运行时上下文：深度与委派链随链路累加。"""
    return HarnessContext(
        user_id=deps.user_id,
        username=deps.username,
        agent_id=agent_id,
        group_id=group_id,
        group_role="leader",
        team_depth=depth,
        delegation_chain=chain,
    )


class GroupRunner:
    """按需构建目标组组长图，并等待其返回结论。"""

    def __init__(self, deps: TeamDeps) -> None:
        self._deps = deps

    async def delegate(
        self,
        caller: TeamAgentContext,
        target_group_id: str,
        task: str,
        expected_output: str = "",
        *,
        depth: int = 0,
        chain: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        assert caller.group_context is not None
        caller_group = caller.group_context.active_group
        if not caller.is_active_leader:
            return _error("permission_denied", "只有组长可以发起跨组委派")
        if not target_group_id or target_group_id == caller_group.group_id:
            return _error("self_delegation", "不能委派给自己所在的组")
        if target_group_id in chain:
            return _error("circular_delegation", "目标组已在委派链中，拒绝循环委派")
        if depth + 1 > int(agent_config.AGENT_TEAM_MAX_DEPTH):
            return _error(
                "depth_exceeded",
                f"跨组委派深度超过 {agent_config.AGENT_TEAM_MAX_DEPTH}",
            )

        group = await self._deps.group_store.get(target_group_id)
        if group is None:
            return _error("group_not_found", f"组不存在: {target_group_id}")
        if group.get("visibility") != "discoverable":
            return _error("group_not_visible", "目标组未开放跨组协作")

        leader_id = str(group.get("leader_agent_id") or "")
        if not leader_id:
            return _error("group_invalid", "目标组没有组长")

        semaphore = _SEMAPHORE.setdefault(
            self._deps.user_id,
            asyncio.Semaphore(int(agent_config.AGENT_TEAM_MAX_CONCURRENT)),
        )
        # 忙则拒绝，不排队：排队会把调用方的 run 拖到超时。
        if not semaphore.acquire(blocking=False):
            return _error("busy", "当前跨组委派繁忙，请稍后重试")
        try:
            return await asyncio.wait_for(
                self._run(
                    leader_id,
                    target_group_id,
                    task,
                    expected_output,
                    group,
                    depth=depth + 1,
                    chain=(*chain, target_group_id),
                ),
                timeout=float(agent_config.AGENT_TEAM_DELEGATION_TIMEOUT),
            )
        except asyncio.TimeoutError:
            return _error(
                "timeout",
                f"目标组超过 {agent_config.AGENT_TEAM_DELEGATION_TIMEOUT}s 未返回",
            )
        except Exception as error:  # noqa: BLE001 - 工具边界必须结构化返回
            logger.exception("跨组委派失败：%s", target_group_id)
            return _error("delegation_failed", f"{type(error).__name__}: {error}")
        finally:
            semaphore.release()

    async def _run(
        self,
        leader_id: str,
        group_id: str,
        task: str,
        expected_output: str,
        group: dict[str, Any],
        *,
        depth: int,
        chain: tuple[str, ...],
    ) -> dict[str, Any]:
        factory = AgentContextFactory(self._deps.store, self._deps.user_id)
        context = await factory.build(leader_id, group_id)

        # 延迟导入避免循环依赖：runner -> main_agent -> assembly/tools -> runner
        from agent.main_agent import create_main_agent

        thread_id = f"team-{uuid4().hex}"
        graph = await create_main_agent(
            self._deps.create_config(thread_id),
            store=self._deps.store,
            checkpointer=self._deps.checkpointer,
            sandbox_backend=self._deps.sandbox_backend,
            integration_server_id=self._deps.integration_server_id,
            agent_context=context,
        )
        prompt = task if not expected_output else f"{task}\n\n期望产出：{expected_output}"
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config=self._deps.create_config(thread_id),
            context=build_delegation_context(
                self._deps, group_id, leader_id, depth=depth, chain=chain
            ),
        )
        answer = _last_ai_text(result)
        return {
            "ok": True,
            "group": {"group_id": group_id, "name": group["name"]},
            "leader_agent_id": leader_id,
            "summary": answer[:8000],
            "truncated": len(answer) > 8000,
        }
