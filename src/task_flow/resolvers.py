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
    """SKILL 是否存在：查当前用户技能库 main/ 与 subagents/*/ 下的目录。"""
    try:
        from agent.identity import current_user_id
        from agent.user_data import user_skills_root

        skills_root = user_skills_root(current_user_id())
        for target in (skills_root / "main" / name,):
            if target.is_dir():
                return True
        subagents_root = skills_root / "subagents"
        if subagents_root.is_dir():
            for agent_dir in subagents_root.iterdir():
                if agent_dir.is_dir() and (agent_dir / name).is_dir():
                    return True
        # skills-disabled 里的视为不存在（已停用，Agent 不会发现）
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
