"""MyAgent 档案：用户可招募/编辑的多个主 Agent。

每个用户可以拥有一个默认主 Agent 和任意数量的自定义 Agent。档案存放在
PostgreSQL Store 的 ``("agent_profiles", user_id)`` 命名空间，与会话、经验、
轨迹使用同一套隔离机制。

一个档案决定三件事：

* 角色系统提示词（叠加在 Harness 固定的执行/安全策略之上）；
* 可选的独立 LLM 配置（未配置时使用系统设置里的全局模型）；
* 允许主 Agent 连接的 MCP 服务集合（空表示全部公共 MCP）。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

AGENT_NAMESPACE_PREFIX = ("agent_profiles",)
DEFAULT_AGENT_ID = "default"
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_SYSTEM_PROMPT = 20_000

# 默认主 Agent 的角色卡唯一来源是 ``agent/memory/prompts.py``：
# 新用户、以及从未改过提示词的老用户，看到和使用的都是代码里的「总管」。
from agent.memory.prompts import system_prompt as CODE_DEFAULT_AGENT_PROMPT

DEFAULT_SYSTEM_PROMPT = CODE_DEFAULT_AGENT_PROMPT.strip()

# 旧版默认角色卡（「大肥鱼」）的首行标记：用于识别从未改过提示词的老档案。
LEGACY_FISH_PROMPT_MARKER = "# 大肥鱼"

# 早期版本写入数据库的占位提示词；迁移时替换为代码角色卡。
LEGACY_DEFAULT_SYSTEM_PROMPT = """你是 Harness 的默认主 Agent，一名可靠的通用任务执行者。

工作原则：
1. 先理解目标与约束，再动手；信息不足时先向用户确认关键前提。
2. 复杂任务先拆解并说明计划，再按波次执行；独立子任务并行推进。
3. 所有结论必须有依据；不确定时明确说明不确定点，不编造事实。
4. 文件与命令严格遵守运行环境约束：命令只在沙箱执行，文件写入当前项目。
5. 交付前自检：目标是否达成、约束是否满足、结果是否可复现。
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _namespace(user_id: str) -> tuple[str, str]:
    return (*AGENT_NAMESPACE_PREFIX, user_id)


def _clean_id(value: str | None) -> str:
    raw = str(value or "").strip()
    return raw if AGENT_ID_PATTERN.fullmatch(raw) else ""


def default_profile() -> dict[str, Any]:
    return {
        "id": DEFAULT_AGENT_ID,
        "name": "默认主 Agent",
        "role_title": "通用任务执行者",
        "description": "Harness 内置主 Agent，可直接使用，也可在 MyAgent 中修改提示词与模型。",
        "avatar": "H",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "model": {},
        "mcp_server_ids": [],
        "enabled": True,
        "is_default": True,
        "created_at": _now(),
        "updated_at": _now(),
    }


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """对外返回时隐藏模型密钥。"""
    data = dict(profile)
    model = dict(data.get("model") or {})
    model["has_api_key"] = bool(model.pop("api_key", ""))
    data["model"] = model
    return data


def normalize_model(raw: Any) -> dict[str, Any]:
    """裁剪模型配置字段；Agent 档案与组模型共用同一套白名单。"""
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "model_name",
        "base_url",
        "api_key",
        "thinking_enabled",
        "context_limit",
    }
    model = {key: raw[key] for key in allowed if raw.get(key) not in (None, "")}
    if "context_limit" in model:
        try:
            model["context_limit"] = int(model["context_limit"])
        except (TypeError, ValueError):
            model.pop("context_limit", None)
    if "thinking_enabled" in model:
        model["thinking_enabled"] = bool(model["thinking_enabled"])
    return model


def _needs_default_prompt_migration(profile: dict[str, Any]) -> bool:
    """判断默认档案是否仍是旧占位提示词或旧角色卡（用户改过则保留）。"""
    if not bool(profile.get("is_default")):
        return False
    prompt = str(profile.get("system_prompt") or "").strip()
    if not prompt or prompt == LEGACY_DEFAULT_SYSTEM_PROMPT.strip():
        return True
    # 旧版「大肥鱼」角色卡：仅当档案仍以该角色卡开头时视为从未改动。
    return prompt.startswith(LEGACY_FISH_PROMPT_MARKER)


def _normalize_mcp_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    ids = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in ids:
            ids.append(value[:64])
    return ids


class AgentProfileStore:
    """按用户读写 Agent 档案。"""

    def __init__(self, store: Any, user_id: str) -> None:
        self._store = store
        self._user_id = user_id
        self._namespace = _namespace(user_id)

    async def ensure_default(self) -> dict[str, Any]:
        existing = await self._store.aget(self._namespace, DEFAULT_AGENT_ID)
        if existing is not None:
            value = dict(existing.value)
            # 早期版本写入的是通用占位提示词；用户没改过时替换为代码角色卡。
            if _needs_default_prompt_migration(value):
                value["system_prompt"] = DEFAULT_SYSTEM_PROMPT
                value["is_default"] = True
                value["updated_at"] = _now()
                await self._store.aput(
                    self._namespace, DEFAULT_AGENT_ID, value, index=False
                )
                logger.info("已将默认主 Agent 提示词迁移为代码角色卡")
            return value
        profile = default_profile()
        await self._store.aput(
            self._namespace, DEFAULT_AGENT_ID, profile, index=False
        )
        return profile

    async def list(self) -> list[dict[str, Any]]:
        await self.ensure_default()
        items = await self._store.asearch(self._namespace, limit=500)
        profiles = [dict(item.value) for item in items]
        profiles.sort(
            key=lambda item: (not bool(item.get("is_default")), item.get("name", ""))
        )
        return profiles

    async def get(self, agent_id: str) -> dict[str, Any] | None:
        cleaned = _clean_id(agent_id) or DEFAULT_AGENT_ID
        if cleaned == DEFAULT_AGENT_ID:
            # 默认档案每次读取都走迁移逻辑，保证老用户也能拿到代码角色卡。
            return await self.ensure_default()
        item = await self._store.aget(self._namespace, cleaned)
        return dict(item.value) if item is not None else None

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("Agent 名称不能为空")
        agent_id = _clean_id(payload.get("id")) or f"agent_{uuid.uuid4().hex[:8]}"
        if agent_id == DEFAULT_AGENT_ID:
            raise ValueError("默认 Agent ID 已被占用")
        if await self.get(agent_id) is not None:
            raise ValueError(f"Agent 已存在: {agent_id}")
        now = _now()
        profile = {
            "id": agent_id,
            "name": name,
            "role_title": str(payload.get("role_title") or "自定义员工")[:80],
            "description": str(payload.get("description") or "")[:500],
            "avatar": str(payload.get("avatar") or name[:1]).strip()[:2] or "A",
            "system_prompt": str(
                payload.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
            )[:MAX_SYSTEM_PROMPT],
            "model": normalize_model(payload.get("model")),
            "mcp_server_ids": _normalize_mcp_ids(payload.get("mcp_server_ids")),
            "enabled": bool(payload.get("enabled", True)),
            "is_default": False,
            "created_at": now,
            "updated_at": now,
        }
        await self._store.aput(self._namespace, agent_id, profile, index=False)
        return profile

    async def update(
        self, agent_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        cleaned = _clean_id(agent_id) or DEFAULT_AGENT_ID
        profile = await self.get(cleaned)
        if profile is None:
            return None
        updated = dict(profile)
        if "name" in patch:
            name = str(patch.get("name") or "").strip()[:80]
            if not name:
                raise ValueError("Agent 名称不能为空")
            updated["name"] = name
        if "role_title" in patch:
            updated["role_title"] = str(patch.get("role_title") or "")[:80]
        if "description" in patch:
            updated["description"] = str(patch.get("description") or "")[:500]
        if "avatar" in patch:
            updated["avatar"] = str(patch.get("avatar") or "")[:2] or "A"
        if "system_prompt" in patch:
            prompt = str(patch.get("system_prompt") or "")
            if not prompt.strip():
                raise ValueError("系统提示词不能为空")
            updated["system_prompt"] = prompt[:MAX_SYSTEM_PROMPT]
        if "model" in patch:
            # 前端回传空字符串表示“沿用全局模型”，不保存空键。
            updated["model"] = normalize_model(patch.get("model"))
        if "mcp_server_ids" in patch:
            updated["mcp_server_ids"] = _normalize_mcp_ids(
                patch.get("mcp_server_ids")
            )
        if "enabled" in patch:
            updated["enabled"] = bool(patch.get("enabled"))
        updated["updated_at"] = _now()
        await self._store.aput(self._namespace, cleaned, updated, index=False)
        return updated

    async def delete(self, agent_id: str) -> bool:
        cleaned = _clean_id(agent_id)
        if not cleaned or cleaned == DEFAULT_AGENT_ID:
            raise ValueError("默认 Agent 不可删除")
        if await self.get(cleaned) is None:
            return False
        await self._store.adelete(self._namespace, cleaned)
        return True

    @staticmethod
    def merge_model(profile: dict[str, Any], user_model: dict[str, Any]) -> dict[str, Any]:
        """档案模型配置覆盖全局模型；未配置的字段继续继承。"""
        merged = dict(user_model)
        override = normalize_model(profile.get("model"))
        merged.update(override)
        return merged
