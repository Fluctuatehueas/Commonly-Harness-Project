"""命令系统资源绑定：归一化、缓存哈希与合法性校验。

绑定只作用于下一个自然轮，由前端随 ``ChatRequest.binding`` 传入；
后端在进入构图前完成校验，避免把不存在的资源静默忽略。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MAX_BINDING_ITEMS = 8
BINDING_KEYS = ("skills", "mcp_ids", "plugin_ids")


def _clean(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return list(dict.fromkeys(cleaned))[:MAX_BINDING_ITEMS]


def normalize_binding(raw: Any) -> dict[str, list[str]] | None:
    """把请求里的绑定归一化；空绑定返回 ``None``。"""
    if not raw:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return None
    data = {key: _clean(raw.get(key)) for key in BINDING_KEYS}
    if not any(data.values()):
        return None
    return data


def binding_hash(raw: Any) -> str:
    """稳定短哈希，用于 Agent 图缓存键；空绑定返回空串。"""
    data = normalize_binding(raw)
    if not data:
        return ""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


async def validate_binding(user_id: str, raw: Any) -> list[str]:
    """返回不合法绑定项；空列表表示全部有效。"""
    data = normalize_binding(raw)
    if not data:
        return []
    problems: list[str] = []

    if data["skills"]:
        from agent.config import SKILLS_ROOT
        from agent.user_data import builtin_disabled_keys, user_skills_root

        user_root = user_skills_root(user_id) / "main"
        builtin_root = Path(SKILLS_ROOT) / "main"
        disabled = builtin_disabled_keys(user_id)
        for name in data["skills"]:
            if (user_root / name / "SKILL.md").is_file():
                continue
            if (
                (builtin_root / name / "SKILL.md").is_file()
                and f"main:{name}" not in disabled
            ):
                continue
            problems.append(f"技能不存在或已停用: {name}")

    if data["mcp_ids"]:
        from agent.mcp.runtime import get_mcp_runtime

        known = set(get_mcp_runtime(user_id).server_ids())
        for item in data["mcp_ids"]:
            if item not in known:
                problems.append(f"MCP 服务不存在: {item}")

    if data["plugin_ids"]:
        from plugin_bus.runtime import get_plugin_runtime

        runtime = get_plugin_runtime(user_id)
        await runtime.initialize()
        known = set(runtime.installed_map().keys())
        for item in data["plugin_ids"]:
            if item not in known:
                problems.append(f"插件未安装: {item}")

    return problems


__all__ = [
    "BINDING_KEYS",
    "MAX_BINDING_ITEMS",
    "binding_hash",
    "normalize_binding",
    "validate_binding",
]
