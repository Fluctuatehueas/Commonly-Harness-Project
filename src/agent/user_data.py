"""用户级数据目录、配置与资源隔离。

目录约定::

    <USER_DATA_DIR>/users/<user_id>/
        settings.json          # 模型 / 沙箱 / 上下文 / 归档等系统配置
        projects.json          # 该用户的项目登记（由 project_store 维护）
        rag/providers.json     # 该用户的 RAG Provider 配置

    <HARNESS_HOME>/mcp/users/<user_id>/
        servers.json           # 该用户的 MCP 服务配置
        runtime/<server_id>/   # npm 缓存与 stdio 日志

    <HARNESS_HOME>/skills/
        builtin/               # 内置技能（只读）
        users/<user_id>/       # 用户技能与 skills-disabled
        users/<user_id>/skills-state.json

``HARNESS_HOME`` 由 PUBLISH_PROGRAM_PATH 决定，未设置或不可写时回退
``USER_DATA_DIR``。所有读写都做目录名清洗与原子写入。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent.identity import safe_user_id

logger = logging.getLogger(__name__)

USER_SETTINGS_SECTIONS = ("model", "sandbox", "context", "archive")


def _config_module() -> Any:
    """延迟导入，避免 agent.config 与 user_data 的循环依赖。"""
    from agent import config as agent_config

    return agent_config


def user_root(user_id: str | None) -> Path:
    config = _config_module()
    root = Path(config.USER_DATA_DIR) / "users" / safe_user_id(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_settings_path(user_id: str | None) -> Path:
    return user_root(user_id) / "settings.json"


def user_project_path(user_id: str | None) -> Path:
    return user_root(user_id) / "projects.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.is_file():
            return default
        raw = json.loads(path.read_text(encoding="utf-8-sig") or "null")
    except (OSError, json.JSONDecodeError):
        logger.warning("读取用户配置失败，使用默认值：%s", path, exc_info=True)
        return default
    return raw if raw is not None else default


def write_json(path: Path, data: Any) -> None:
    """原子写入：先写临时文件再替换，失败不破坏原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


# ============================================================
# 系统配置（按用户）
# ============================================================


def read_user_settings(user_id: str | None) -> dict[str, dict[str, Any]]:
    raw = read_json(user_settings_path(user_id), {})
    settings: dict[str, dict[str, Any]] = {}
    for section in USER_SETTINGS_SECTIONS:
        value = raw.get(section) if isinstance(raw, dict) else None
        settings[section] = dict(value) if isinstance(value, dict) else {}
    return settings


def update_user_settings(
    user_id: str | None,
    patch: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """按 section 合并写入；只覆盖显式提供的键，``None`` 视为不修改。"""
    current = read_user_settings(user_id)
    for section, values in patch.items():
        if section not in USER_SETTINGS_SECTIONS or not isinstance(values, dict):
            continue
        target = current.setdefault(section, {})
        for key, value in values.items():
            if value is None:
                continue
            target[key] = value
    write_json(user_settings_path(user_id), current)
    return current


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "off", "no"}


def global_model_settings() -> dict[str, Any]:
    """部署级全局模型配置：唯一来源是 .env，由系统设置页维护。"""
    try:
        context_limit = int(os.getenv("HARNESS_MODEL_CONTEXT_LIMIT") or 128000)
    except (TypeError, ValueError):
        context_limit = 128000
    return {
        "model_name": str(os.getenv("DEEPSEEK_MODEL_NAME") or "deepseek-v4-flash"),
        "base_url": str(os.getenv("DEEPSEEK_BASE_URL") or ""),
        "api_key": str(os.getenv("DEEPSEEK_API_KEY") or ""),
        "context_limit": context_limit,
    }


def model_settings(user_id: str | None) -> dict[str, Any]:
    """当前用户实际生效的模型配置：全局模型 + 用户级思考/联网开关。

    模型名、端点、密钥与上下文窗口只由系统设置里的全局模型（.env）决定；
    用户 settings.json 中遗留的模型字段不再参与解析。
    """
    config = _config_module()
    override = read_user_settings(user_id)["model"]
    thinking = override.get("thinking_enabled")
    if thinking is None:
        thinking = bool(config.MODEL_THINKING_ENABLED)
    web_search = override.get("web_search_enabled")
    if web_search is None:
        web_search = bool(config.WEB_SEARCH_ENABLED)
    return {
        **global_model_settings(),
        "thinking_enabled": bool(thinking),
        "web_search_enabled": bool(web_search),
    }


def public_model_settings(user_id: str | None) -> dict[str, Any]:
    """对外返回时隐藏密钥，只暴露是否已配置。"""
    settings = model_settings(user_id)
    return {
        "model_name": settings["model_name"],
        "base_url": settings["base_url"],
        "has_api_key": bool(settings["api_key"]),
        "thinking_enabled": settings["thinking_enabled"],
        "web_search_enabled": settings["web_search_enabled"],
        "context_limit": settings["context_limit"],
    }


def sandbox_settings(user_id: str | None) -> dict[str, Any]:
    config = _config_module()
    override = read_user_settings(user_id)["sandbox"]

    def pick(key: str, default: Any) -> Any:
        value = override.get(key)
        return default if value is None else value

    return {
        "host": str(pick("host", config.OPEN_SANDBOX_HOST)),
        "port": int(pick("port", config.OPEN_SANDBOX_PORT)),
        "api_key": str(pick("api_key", config.OPEN_SANDBOX_API_KEY or "") or ""),
        "image": str(pick("image", config.OPEN_SANDBOX_IMAGE)),
        "default_timeout_seconds": int(
            pick(
                "default_timeout_seconds",
                config.OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS,
            )
        ),
        "sandbox_timeout_seconds": int(
            pick("sandbox_timeout_seconds", config.OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS)
        ),
        "prewarm_enabled": bool(
            pick("prewarm_enabled", config.OPEN_SANDBOX_PREWARM_ENABLED)
        ),
        "use_server_proxy": bool(
            pick("use_server_proxy", config.OPEN_SANDBOX_USE_SERVER_PROXY)
        ),
    }


def context_settings(user_id: str | None) -> dict[str, Any]:
    config = _config_module()
    override = read_user_settings(user_id)["context"]

    def pick(key: str, default: Any) -> Any:
        value = override.get(key)
        return default if value is None else value

    return {
        "enabled": bool(pick("enabled", config.CONTEXT_COMPACT_ENABLED)),
        "trigger": float(pick("trigger", config.CONTEXT_COMPACT_TRIGGER)),
        "hard": float(pick("hard", config.CONTEXT_COMPACT_HARD)),
        "keep": float(pick("keep", config.CONTEXT_COMPACT_KEEP)),
        "alert_threshold": float(
            pick("alert_threshold", config.CONTEXT_ALERT_THRESHOLD)
        ),
        "min_rounds": int(pick("min_rounds", config.CONTEXT_COMPACT_MIN_ROUNDS)),
        "cooldown": int(pick("cooldown", config.CONTEXT_COMPACT_COOLDOWN)),
        "max_defer": int(pick("max_defer", config.CONTEXT_COMPACT_MAX_DEFER)),
        "timeout_seconds": int(pick("timeout_seconds", config.CONTEXT_COMPACT_TIMEOUT)),
        "eval_enabled": bool(
            pick("eval_enabled", config.CONTEXT_COMPACT_EVAL_ENABLED)
        ),
        "eval_judge": bool(pick("eval_judge", config.CONTEXT_COMPACT_EVAL_JUDGE)),
        "heartbeat_seconds": int(
            pick("heartbeat_seconds", config.CONTEXT_COMPACT_HEARTBEAT)
        ),
    }


def archive_settings(user_id: str | None) -> dict[str, int]:
    override = read_user_settings(user_id)["archive"]
    try:
        days = int(override.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    return {"days": max(days, 0)}


# ============================================================
# MCP / RAG / 技能：按用户资源目录
# ============================================================


def _copy_file_once(source: Path, target: Path) -> None:
    if target.exists() or not source.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError:
        logger.warning("初始化用户配置副本失败：%s -> %s", source, target, exc_info=True)


def user_mcp_config_path(user_id: str | None) -> Path:
    config = _config_module()
    from agent.storage_migration import migrate_user

    migrate_user(user_id)
    target = (
        Path(config.HARNESS_HOME)
        / "mcp"
        / "users"
        / safe_user_id(user_id)
        / "servers.json"
    )
    _copy_file_once(Path(config.MCP_CONFIG_PATH), target)
    return target


def user_mcp_root(user_id: str | None) -> Path:
    config = _config_module()
    return Path(config.HARNESS_HOME) / "mcp" / "users" / safe_user_id(user_id)


def user_rag_config_path(user_id: str | None) -> Path:
    config = _config_module()
    target = user_root(user_id) / "rag" / "providers.json"
    _copy_file_once(Path(config.RAG_CONFIG_PATH), target)
    return target


def user_skills_root(user_id: str | None) -> Path:
    """用户技能库：``<HARNESS_HOME>/skills/users/{uid}``。

    内置技能不再复制进来，Agent 读取时先用户、后 builtin。
    """
    config = _config_module()
    from agent.storage_migration import migrate_user

    migrate_user(user_id)
    target = (
        Path(config.HARNESS_HOME) / "skills" / "users" / safe_user_id(user_id)
    )
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def user_disabled_skills_root(user_id: str | None) -> Path:
    target = user_skills_root(user_id) / "skills-disabled"
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def builtin_skills_root() -> Path:
    """随包内置技能根目录（只读）。"""
    return Path(_config_module().SKILLS_ROOT).resolve()


def skills_state_path(user_id: str | None) -> Path:
    return user_skills_root(user_id) / "skills-state.json"


def read_skills_state(user_id: str | None) -> dict[str, Any]:
    raw = read_json(skills_state_path(user_id), {})
    disabled = raw.get("disabled_builtin") if isinstance(raw, dict) else None
    return {
        "builtin_version": str(
            (raw.get("builtin_version") if isinstance(raw, dict) else "") or ""
        ),
        "disabled_builtin": [
            str(item) for item in (disabled or []) if str(item or "").strip()
        ],
    }


def write_skills_state(user_id: str | None, state: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "builtin_version": str(state.get("builtin_version") or ""),
        "disabled_builtin": sorted(
            {str(item) for item in (state.get("disabled_builtin") or []) if item}
        ),
    }
    write_json(skills_state_path(user_id), payload)
    return payload


def builtin_disabled_keys(user_id: str | None) -> set[str]:
    return set(read_skills_state(user_id)["disabled_builtin"])


def user_paths(user_id: str | None) -> dict[str, str]:
    """设置页展示用的用户级路径集合。"""
    config = _config_module()
    return {
        "user_root": str(user_root(user_id)),
        "harness_home": str(config.HARNESS_HOME),
        "settings": str(user_settings_path(user_id)),
        "projects": str(user_project_path(user_id)),
        "mcp_config": str(user_mcp_config_path(user_id)),
        "mcp_root": str(user_mcp_root(user_id)),
        "rag_config": str(user_rag_config_path(user_id)),
        "skills_root": str(user_skills_root(user_id)),
        "builtin_skills_root": str(builtin_skills_root()),
        "skills_disabled_root": str(user_disabled_skills_root(user_id)),
    }
