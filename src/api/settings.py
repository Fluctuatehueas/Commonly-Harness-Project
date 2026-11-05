"""模型与沙箱配置读写接口：支持 .env 热更新。

- GET  /settings/model   返回当前模型配置（API Key 只回传是否已设置）
- PUT  /settings/model   更新 .env（api_key 留空则保留原值），并热更新运行中的模型
- GET  /settings/sandbox 返回沙箱配置、执行模式与本机运行时探测结果
- PUT  /settings/sandbox 更新沙箱配置并重建沙箱管理器
- POST /settings/sandbox/check 检测沙箱服务端口是否可达
"""

from __future__ import annotations

import os
import re
import socket
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import config as agent_config
from agent.config import PROJECT_ROOT, reload_model_config, reload_sandbox_config
from utils.integrity import integrity_hint, is_low_integrity, process_integrity
from utils.runtime_probe import detect_local_environment


router = APIRouter()

ENV_PATH = PROJECT_ROOT / ".env"
ENV_KEY_MODEL = "DEEPSEEK_MODEL_NAME"
ENV_KEY_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_KEY_API = "DEEPSEEK_API_KEY"
ENV_KEY_THINKING = "HARNESS_MODEL_THINKING"
ENV_KEY_CONTEXT_LIMIT = "HARNESS_MODEL_CONTEXT_LIMIT"
ENV_KEY_WEB_SEARCH = "HARNESS_WEB_SEARCH"
# 沙箱配置键：与 agent.config 中的 OPEN_SANDBOX_* 保持一致。
ENV_KEY_SANDBOX_HOST = "OPEN_SANDBOX_HOST"
ENV_KEY_SANDBOX_PORT = "OPEN_SANDBOX_PORT"
ENV_KEY_SANDBOX_API = "OPEN_SANDBOX_API_KEY"
ENV_KEY_SANDBOX_IMAGE = "OPEN_SANDBOX_IMAGE"
ENV_KEY_SANDBOX_CALL_TIMEOUT = "OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS"
ENV_KEY_SANDBOX_LIFETIME = "OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS"
ENV_KEY_SANDBOX_PREWARM = "OPEN_SANDBOX_PREWARM_ENABLED"
ENV_KEY_SANDBOX_PROXY = "OPEN_SANDBOX_USE_SERVER_PROXY"

# 上下文压缩配置键（v1.5 起支持设置页在线修改）。
ENV_KEY_CTX_ALERT = "HARNESS_CONTEXT_ALERT_THRESHOLD"
ENV_KEY_CTX_ENABLED = "HARNESS_CONTEXT_COMPACT_ENABLED"
ENV_KEY_CTX_TRIGGER = "HARNESS_CONTEXT_COMPACT_TRIGGER"
ENV_KEY_CTX_HARD = "HARNESS_CONTEXT_COMPACT_HARD"
ENV_KEY_CTX_KEEP = "HARNESS_CONTEXT_COMPACT_KEEP"
ENV_KEY_CTX_MIN_ROUNDS = "HARNESS_CONTEXT_COMPACT_MIN_ROUNDS"
ENV_KEY_CTX_COOLDOWN = "HARNESS_CONTEXT_COMPACT_COOLDOWN"
ENV_KEY_CTX_MAX_DEFER = "HARNESS_CONTEXT_COMPACT_MAX_DEFER"
ENV_KEY_CTX_TIMEOUT = "HARNESS_CONTEXT_COMPACT_TIMEOUT"
ENV_KEY_CTX_EVAL = "HARNESS_CONTEXT_COMPACT_EVAL_ENABLED"
ENV_KEY_CTX_JUDGE = "HARNESS_CONTEXT_EVAL_JUDGE"
ENV_KEY_CTX_HEARTBEAT = "HARNESS_CONTEXT_COMPACT_HEARTBEAT"

# 会话自动归档：超过该天数没有新记录的会话会在进入对话页时自动归档。
ENV_KEY_ARCHIVE_DAYS = "HARNESS_SESSION_ARCHIVE_DAYS"
DEFAULT_ARCHIVE_DAYS = 30


class ModelSettingsUpdate(BaseModel):
    """PUT /settings/model 请求体；None 字段不改动，api_key 空串视为不改动。"""

    model_name: str | None = Field(None, min_length=1, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=300)
    thinking_enabled: bool | None = None
    web_search_enabled: bool | None = None
    # 上下文窗口（tokens）。只影响监控 / 压缩阈值展示，不改变模型实际能力。
    context_limit: int | None = Field(None, ge=16000, le=2000000)


def _update_env_file(updates: dict[str, str]) -> None:
    """按键替换 .env 中的行；未出现的键追加到文件末尾，保留其余内容与注释。"""
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line.strip())
        if match and match.group(1) in remaining:
            key = match.group(1)
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")


def _current_state() -> dict[str, Any]:
    return {
        "model_name": os.getenv(ENV_KEY_MODEL, ""),
        "base_url": os.getenv(ENV_KEY_BASE_URL, ""),
        "has_api_key": bool(os.getenv(ENV_KEY_API)),
        # 读模块属性而非环境变量：reload 后与模型单例实际生效值保持一致。
        "thinking_enabled": bool(agent_config.MODEL_THINKING_ENABLED),
        "context_limit": int(agent_config.MODEL_CONTEXT_LIMIT),
        "web_search_enabled": bool(agent_config.WEB_SEARCH_ENABLED),
    }


@router.get("/settings/model")
async def get_model_settings() -> dict[str, Any]:
    """返回当前生效的模型配置。"""
    return _current_state()


@router.put("/settings/model")
async def update_model_settings(body: ModelSettingsUpdate) -> dict[str, Any]:
    """写入 .env 并热更新模型；API Key 仅在显式提供非空值时更新。"""
    updates: dict[str, str] = {}
    if body.model_name:
        updates[ENV_KEY_MODEL] = body.model_name.strip()
    if body.base_url is not None:
        value = body.base_url.strip()
        if value and not value.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="base_url 必须以 http:// 或 https:// 开头")
        updates[ENV_KEY_BASE_URL] = value
    if body.api_key:
        updates[ENV_KEY_API] = body.api_key.strip()
    if body.thinking_enabled is not None:
        updates[ENV_KEY_THINKING] = "true" if body.thinking_enabled else "false"
    if body.web_search_enabled is not None:
        updates[ENV_KEY_WEB_SEARCH] = "true" if body.web_search_enabled else "false"
    if body.context_limit is not None:
        updates[ENV_KEY_CONTEXT_LIMIT] = str(int(body.context_limit))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        _update_env_file(updates)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {error}") from error

    # 热更新：重置模型单例 + 清空按用户缓存的 Agent 图
    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_agents()
    return {"ok": True, "updated": sorted(updates), "current": _current_state()}


# ============================================================
# 上下文压缩配置
# ============================================================


class ContextSettingsUpdate(BaseModel):
    """PUT /settings/context 请求体；None 表示不修改。"""

    enabled: bool | None = None
    trigger: float | None = Field(None, gt=0, le=1)
    hard: float | None = Field(None, gt=0, le=1)
    keep: float | None = Field(None, gt=0, le=1)
    alert_threshold: float | None = Field(None, gt=0, le=1)
    min_rounds: int | None = Field(None, ge=1, le=100)
    cooldown: int | None = Field(None, ge=0, le=50)
    max_defer: int | None = Field(None, ge=0, le=20)
    timeout_seconds: int | None = Field(None, ge=5, le=300)
    eval_enabled: bool | None = None
    eval_judge: bool | None = None
    heartbeat_seconds: int | None = Field(None, ge=2, le=60)


def _context_state() -> dict[str, Any]:
    """返回当前生效的压缩配置与推导出的预算数字。"""
    from agent.context.budget import build_budget

    budget = build_budget()
    return {
        "enabled": bool(agent_config.CONTEXT_COMPACT_ENABLED),
        "trigger": float(agent_config.CONTEXT_COMPACT_TRIGGER),
        "hard": float(agent_config.CONTEXT_COMPACT_HARD),
        "keep": float(agent_config.CONTEXT_COMPACT_KEEP),
        "alert_threshold": float(agent_config.CONTEXT_ALERT_THRESHOLD),
        "min_rounds": int(agent_config.CONTEXT_COMPACT_MIN_ROUNDS),
        "cooldown": int(agent_config.CONTEXT_COMPACT_COOLDOWN),
        "max_defer": int(agent_config.CONTEXT_COMPACT_MAX_DEFER),
        "timeout_seconds": int(agent_config.CONTEXT_COMPACT_TIMEOUT),
        "eval_enabled": bool(agent_config.CONTEXT_COMPACT_EVAL_ENABLED),
        "eval_judge": bool(agent_config.CONTEXT_COMPACT_EVAL_JUDGE),
        "heartbeat_seconds": int(agent_config.CONTEXT_COMPACT_HEARTBEAT),
        "budget": budget.as_dict(),
    }


@router.get("/settings/context")
async def get_context_settings() -> dict[str, Any]:
    """返回当前上下文压缩配置。"""
    return _context_state()


@router.put("/settings/context")
async def update_context_settings(body: ContextSettingsUpdate) -> dict[str, Any]:
    """写入 .env 并热重建 Agent 图，使新阈值立即生效。"""
    current = _context_state()
    trigger = float(body.trigger if body.trigger is not None else current["trigger"])
    hard = float(body.hard if body.hard is not None else current["hard"])
    keep = float(body.keep if body.keep is not None else current["keep"])
    if not (0 < keep < trigger < hard <= 1):
        raise HTTPException(
            status_code=422,
            detail="必须满足 0 < keep < trigger < hard <= 1",
        )

    updates: dict[str, str] = {}
    if body.enabled is not None:
        updates[ENV_KEY_CTX_ENABLED] = "true" if body.enabled else "false"
    if body.trigger is not None:
        updates[ENV_KEY_CTX_TRIGGER] = f"{trigger:g}"
    if body.hard is not None:
        updates[ENV_KEY_CTX_HARD] = f"{hard:g}"
    if body.keep is not None:
        updates[ENV_KEY_CTX_KEEP] = f"{keep:g}"
    if body.alert_threshold is not None:
        updates[ENV_KEY_CTX_ALERT] = f"{body.alert_threshold:g}"
    if body.min_rounds is not None:
        updates[ENV_KEY_CTX_MIN_ROUNDS] = str(body.min_rounds)
    if body.cooldown is not None:
        updates[ENV_KEY_CTX_COOLDOWN] = str(body.cooldown)
    if body.max_defer is not None:
        updates[ENV_KEY_CTX_MAX_DEFER] = str(body.max_defer)
    if body.timeout_seconds is not None:
        updates[ENV_KEY_CTX_TIMEOUT] = str(body.timeout_seconds)
    if body.eval_enabled is not None:
        updates[ENV_KEY_CTX_EVAL] = "true" if body.eval_enabled else "false"
    if body.eval_judge is not None:
        updates[ENV_KEY_CTX_JUDGE] = "true" if body.eval_judge else "false"
    if body.heartbeat_seconds is not None:
        updates[ENV_KEY_CTX_HEARTBEAT] = str(body.heartbeat_seconds)
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        _update_env_file(updates)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {error}") from error

    # 热更新：重载配置常量，并清空用户级 Agent 图（下一次请求按新阈值重建）。
    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_agents()
    return {"ok": True, "updated": sorted(updates), "current": _context_state()}


# ============================================================
# 会话自动归档配置
# ============================================================


class ArchiveSettingsUpdate(BaseModel):
    """PUT /settings/archive 请求体；0 表示关闭自动归档。"""

    days: int = Field(..., ge=0, le=3650)


def _archive_state() -> dict[str, Any]:
    raw = os.getenv(ENV_KEY_ARCHIVE_DAYS, "").strip()
    try:
        days = int(raw) if raw else DEFAULT_ARCHIVE_DAYS
    except ValueError:
        days = DEFAULT_ARCHIVE_DAYS
    return {"days": max(days, 0)}


@router.get("/settings/archive")
async def get_archive_settings() -> dict[str, Any]:
    """返回自动归档天数配置。"""
    return _archive_state()


@router.put("/settings/archive")
async def update_archive_settings(body: ArchiveSettingsUpdate) -> dict[str, Any]:
    """写入 .env 并同步运行中的环境变量，下一次进入对话页生效。"""
    try:
        _update_env_file({ENV_KEY_ARCHIVE_DAYS: str(body.days)})
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {error}") from error
    os.environ[ENV_KEY_ARCHIVE_DAYS] = str(body.days)
    return {"ok": True, "current": _archive_state()}


# ============================================================
# 沙箱配置
# ============================================================


class SandboxSettingsUpdate(BaseModel):
    """PUT /settings/sandbox 请求体；None 表示不修改，api_key 空串保留原值。"""

    host: str | None = Field(None, max_length=200)
    port: int | None = Field(None, ge=1, le=65535)
    api_key: str | None = Field(None, max_length=400)
    clear_api_key: bool = False
    image: str | None = Field(None, max_length=300)
    default_timeout_seconds: int | None = Field(None, ge=10, le=3600)
    sandbox_timeout_seconds: int | None = Field(None, ge=600, le=30 * 86400)
    prewarm_enabled: bool | None = None
    use_server_proxy: bool | None = None


def _sandbox_state() -> dict[str, Any]:
    """返回当前沙箱配置（密钥只回传是否已设置）。"""
    configured = bool(agent_config.OPEN_SANDBOX_API_KEY)
    if configured:
        notice = ""
    else:
        notice = (
            "未配置沙箱服务，当前为项目工作区模式：Agent 只能读写项目文件，不能执行命令。"
        )
    return {
        "configured": configured,
        "mode": "sandbox" if configured else "local",
        "host": agent_config.OPEN_SANDBOX_HOST,
        "port": agent_config.OPEN_SANDBOX_PORT,
        "has_api_key": configured,
        "image": agent_config.OPEN_SANDBOX_IMAGE,
        "default_timeout_seconds": agent_config.OPEN_SANDBOX_DEFAULT_TIMEOUT_SECONDS,
        "sandbox_timeout_seconds": agent_config.OPEN_SANDBOX_SANDBOX_TIMEOUT_SECONDS,
        "prewarm_enabled": agent_config.OPEN_SANDBOX_PREWARM_ENABLED,
        "use_server_proxy": agent_config.OPEN_SANDBOX_USE_SERVER_PROXY,
        "notice": notice,
    }


@router.get("/settings/sandbox")
async def get_sandbox_settings() -> dict[str, Any]:
    """返回沙箱配置、执行模式、本机运行时探测与存储路径。"""
    from api.agent_loader import agent_loader

    local_env = detect_local_environment()
    return {
        "sandbox": _sandbox_state(),
        "status": agent_loader.sandbox_status(),
        "local_env": local_env,
        "integrity": {
            "level": process_integrity(),
            "low_integrity": is_low_integrity(),
            "hint": integrity_hint(),
        },
        "paths": {
            **agent_config.user_data_info(),
            "workspace_root": str(agent_config.USER_DATA_DIR),
            "mcp_root": str(agent_config.MCP_ROOT),
            "mcp_config": str(agent_config.MCP_CONFIG_PATH),
            "skills_root": str(agent_config.SKILLS_ROOT),
        },
    }


@router.get("/settings/sandbox/status")
async def get_sandbox_status() -> dict[str, Any]:
    """轻量返回执行模式，供对话页展示本地运行安全提示。"""
    from api.agent_loader import agent_loader

    return agent_loader.sandbox_status()


@router.put("/settings/sandbox")
async def update_sandbox_settings(body: SandboxSettingsUpdate) -> dict[str, Any]:
    """写入 .env 并重建沙箱管理器；api_key 留空时保留原值。"""
    updates: dict[str, str] = {}
    if body.host is not None:
        updates[ENV_KEY_SANDBOX_HOST] = body.host.strip() or "127.0.0.1"
    if body.port is not None:
        updates[ENV_KEY_SANDBOX_PORT] = str(body.port)
    if body.clear_api_key:
        updates[ENV_KEY_SANDBOX_API] = ""
    elif body.api_key:
        updates[ENV_KEY_SANDBOX_API] = body.api_key.strip()
    if body.image is not None:
        updates[ENV_KEY_SANDBOX_IMAGE] = body.image.strip() or "myagent-sandbox:1"
    if body.default_timeout_seconds is not None:
        updates[ENV_KEY_SANDBOX_CALL_TIMEOUT] = str(body.default_timeout_seconds)
    if body.sandbox_timeout_seconds is not None:
        updates[ENV_KEY_SANDBOX_LIFETIME] = str(body.sandbox_timeout_seconds)
    if body.prewarm_enabled is not None:
        updates[ENV_KEY_SANDBOX_PREWARM] = "true" if body.prewarm_enabled else "false"
    if body.use_server_proxy is not None:
        updates[ENV_KEY_SANDBOX_PROXY] = "true" if body.use_server_proxy else "false"
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        _update_env_file(updates)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {error}") from error

    reload_sandbox_config()
    from api.agent_loader import agent_loader

    await agent_loader.reload_sandbox()
    return {
        "ok": True,
        "updated": sorted(updates),
        "sandbox": _sandbox_state(),
        "status": agent_loader.sandbox_status(),
    }


@router.post("/settings/sandbox/check")
async def check_sandbox_port(
    host: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """TCP 探测沙箱服务端口；只是连通性检查，不代表凭据有效。

    可传入前端表单中的 host/port，未传时使用已保存配置。
    """
    host = (host or "").strip() or agent_config.OPEN_SANDBOX_HOST
    port = int(port or agent_config.OPEN_SANDBOX_PORT)
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as error:
        return {
            "reachable": False,
            "host": host,
            "port": port,
            "message": f"无法连接 {host}:{port}（{type(error).__name__}）。请确认 OpenSandbox 服务已启动、端口正确且未被防火墙拦截。",
        }
    if not agent_config.OPEN_SANDBOX_API_KEY:
        return {
            "reachable": True,
            "host": host,
            "port": port,
            "message": "端口可达，但尚未配置 API Key，Agent 仍会以本地模式运行。",
        }
    return {
        "reachable": True,
        "host": host,
        "port": port,
        "message": "沙箱服务端口可达，配置已生效。",
    }


# ============================================================
# 评估驱动自动调参：注册 applier
# ============================================================


async def _apply_autotune_patch(patch: dict[str, float], reason: str) -> None:
    """自动调参落地：写入 .env → 重载配置 → 重建用户 Agent 图。"""
    updates = {
        ENV_KEY_CTX_TRIGGER: f"{float(patch['trigger']):g}",
        ENV_KEY_CTX_HARD: f"{float(patch['hard']):g}",
        ENV_KEY_CTX_KEEP: f"{float(patch['keep']):g}",
    }
    _update_env_file(updates)
    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_agents()
    import logging

    logging.getLogger(__name__).info("[AutoTune] 已自动应用阈值：%s（%s）", updates, reason)


from agent.context.autotune import set_applier as _set_autotune_applier  # noqa: E402

_set_autotune_applier(_apply_autotune_patch)
