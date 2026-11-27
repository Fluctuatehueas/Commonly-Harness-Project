"""引用存在性解析器：把结构校验接到项目真实的 SKILL / SubAgent / MCP 注册表。

每个解析器返回 True 存在 / False 不存在 / None 无法确认（后端不可用），
与 validators.ValidationContext 的 Resolver 签名一致。
解析失败不抛异常——校验期不能因为注册表抖动把整个保存流程打挂。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUBAGENT_CONFIGS_DIR = Path(__file__).resolve().parents[1] / "agent" / "subagents" / "configs"
MCP_SERVERS_JSON = PROJECT_ROOT / "mcp" / "servers.json"


def skill_exists(name: str) -> Optional[bool]:
    """SKILL 是否存在：查用户技能库与内置技能（用户同名覆盖内置）。"""
    try:
        from agent.identity import current_user_id
        from agent.user_data import (
            builtin_disabled_keys,
            builtin_skills_root,
            user_skills_root,
        )

        uid = current_user_id()
        skills_root = user_skills_root(uid)
        if (skills_root / "main" / name).is_dir():
            return True
        subagents_root = skills_root / "subagents"
        if subagents_root.is_dir():
            for agent_dir in subagents_root.iterdir():
                if agent_dir.is_dir() and (agent_dir / name).is_dir():
                    return True

        # 用户目录没有时再查内置技能；内置技能按用户停用的视为不存在。
        builtin_root = builtin_skills_root()
        disabled = builtin_disabled_keys(uid)
        if (
            (builtin_root / "main" / name).is_dir()
            and f"main:{name}" not in disabled
        ):
            return True
        builtin_subagents = builtin_root / "subagents"
        if builtin_subagents.is_dir():
            for agent_dir in builtin_subagents.iterdir():
                if not agent_dir.is_dir() or not (agent_dir / name).is_dir():
                    continue
                key = f"subagents/{agent_dir.name}:{name}"
                if key not in disabled:
                    return True
        return False
    except Exception:
        logger.exception("SKILL 注册表不可用")
        return None


def role_exists(role: str) -> Optional[bool]:
    """SubAgent 角色是否存在：查 subagents/configs/<role>.yaml。"""
    try:
        if not SUBAGENT_CONFIGS_DIR.is_dir():
            return None
        return (SUBAGENT_CONFIGS_DIR / f"{role}.yaml").is_file()
    except Exception:
        logger.exception("SubAgent 配置目录不可用")
        return None


def mcp_exists(name: str) -> Optional[bool]:
    """MCP 服务是否已在当前用户的 servers.json 注册。"""
    try:
        from agent.identity import current_user_id
        from agent.user_data import user_mcp_config_path

        path = user_mcp_config_path(current_user_id())
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        servers = data.get("servers") or {}
        return name in servers
    except Exception:
        logger.exception("用户 MCP servers.json 不可用")
        return None


def clear_caches() -> None:
    """配置变更后调用（保留接口；当前已无进程级缓存）。"""
    return None
