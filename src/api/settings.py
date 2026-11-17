"""模型与沙箱配置读写接口：支持 .env 热更新。

- GET  /settings/model         返回当前用户生效的模型配置（API Key 只回传是否已设置）
- PUT  /settings/model         更新用户级运行开关（思考 / 联网）并热更新
- GET  /settings/model/global  返回全局 LLM 配置（.env，所有用户共享）
- PUT  /settings/model/global  写入 .env 并热更新，清空所有用户的 Agent 缓存
- GET  /settings/sandbox       返回沙箱配置、执行模式与本机运行时探测结果
- PUT  /settings/sandbox       更新沙箱配置并重建沙箱管理器
- POST /settings/sandbox/check 检测沙箱服务端口是否可达
"""

from __future__ import annotations

import os
import re
import socket
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent import config as agent_config
from agent import user_data
from agent.config import PROJECT_ROOT, reload_model_config, reload_sandbox_config
from api.identity import effective_user_id
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
    """PUT /settings/model 请求体：仅用户级运行开关；None 字段不改动。

    模型名 / 端点 / 密钥 / 上下文窗口已收归全局模型（.env），不再接受用户级写入。
    """

    thinking_enabled: bool | None = None
    web_search_enabled: bool | None = None


class GlobalModelSettingsUpdate(BaseModel):
    """PUT /settings/model/global 请求体；None 字段不改动，api_key 空串视为不改动。"""

    model_name: str | None = Field(None, min_length=1, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=300)
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


def _current_state(user_id: str) -> dict[str, Any]:
    """读取该用户实际生效的模型配置（用户覆盖 > 部署级默认）。"""
    return user_data.public_model_settings(user_id)


@router.get("/settings/model")
async def get_model_settings(
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """返回当前用户生效的模型配置。"""
    return _current_state(effective_user_id(user_id))


@router.get("/settings/feedback")
async def get_feedback_settings() -> dict[str, Any]:
    """返回消息反馈开关，前端据此决定是否展示赞同/不赞同/重试按钮。"""
    return {"enabled": bool(agent_config.FEEDBACK_ENABLED)}


def _global_model_state() -> dict[str, Any]:
    """全局模型状态（密钥只回传是否已设置）。"""
    settings = user_data.global_model_settings()
    return {
        "model_name": settings["model_name"],
        "base_url": settings["base_url"],
        "has_api_key": bool(settings["api_key"]),
        "context_limit": settings["context_limit"],
        "env_path": str(ENV_PATH),
    }


@router.get("/settings/model/global")
async def get_global_model_settings() -> dict[str, Any]:
    """返回系统设置里的全局 LLM 配置（.env，所有用户共享）。"""
    return _global_model_state()


@router.put("/settings/model/global")
async def update_global_model_settings(
    body: GlobalModelSettingsUpdate,
) -> dict[str, Any]:
    """写入 .env 并热更新；全局模型变化需要清空所有用户的 Agent 缓存。"""
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
    if body.context_limit is not None:
        updates[ENV_KEY_CONTEXT_LIMIT] = str(int(body.context_limit))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        _update_env_file(updates)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {error}") from error

    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_agents()
    return {"ok": True, "updated": sorted(updates), "current": _global_model_state()}


@router.put("/settings/model")
async def update_model_settings(
    body: ModelSettingsUpdate,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """写入当前用户的运行开关（思考 / 联网）并热更新该用户的 Agent 图。"""
    uid = effective_user_id(user_id)
    patch: dict[str, Any] = {}
    if body.thinking_enabled is not None:
        patch["thinking_enabled"] = bool(body.thinking_enabled)
    if body.web_search_enabled is not None:
        patch["web_search_enabled"] = bool(body.web_search_enabled)
    if not patch:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        user_data.update_user_settings(uid, {"model": patch})
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入用户配置失败: {error}") from error

    # 热更新：清空模型缓存，只重建该用户的 Agent 图。
    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_user_agents(uid)
    return {
        "ok": True,
        "updated": sorted(patch),
        "current": _current_state(uid),
    }


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


def _context_state(user_id: str) -> dict[str, Any]:
    """返回该用户的压缩配置与推导出的预算数字。"""
    from agent.context.budget import build_budget

    data = user_data.context_settings(user_id)
    data["budget"] = build_budget().as_dict()
    return data


@router.get("/settings/context")
async def get_context_settings(
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """返回当前用户的上下文压缩配置。"""
    return _context_state(effective_user_id(user_id))


@router.put("/settings/context")
async def update_context_settings(
    body: ContextSettingsUpdate,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """写入当前用户的压缩配置并热重建该用户的 Agent 图。"""
    uid = effective_user_id(user_id)
    current = _context_state(uid)
    trigger = float(body.trigger if body.trigger is not None else current["trigger"])
    hard = float(body.hard if body.hard is not None else current["hard"])
    keep = float(body.keep if body.keep is not None else current["keep"])
    if not (0 < keep < trigger < hard <= 1):
        raise HTTPException(
            status_code=422,
            detail="必须满足 0 < keep < trigger < hard <= 1",
        )

    patch: dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = bool(body.enabled)
    if body.trigger is not None:
        patch["trigger"] = trigger
    if body.hard is not None:
        patch["hard"] = hard
    if body.keep is not None:
        patch["keep"] = keep
    if body.alert_threshold is not None:
        patch["alert_threshold"] = body.alert_threshold
    if body.min_rounds is not None:
        patch["min_rounds"] = body.min_rounds
    if body.cooldown is not None:
        patch["cooldown"] = body.cooldown
    if body.max_defer is not None:
        patch["max_defer"] = body.max_defer
    if body.timeout_seconds is not None:
        patch["timeout_seconds"] = body.timeout_seconds
    if body.eval_enabled is not None:
        patch["eval_enabled"] = bool(body.eval_enabled)
    if body.eval_judge is not None:
        patch["eval_judge"] = bool(body.eval_judge)
    if body.heartbeat_seconds is not None:
        patch["heartbeat_seconds"] = body.heartbeat_seconds
    if not patch:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        user_data.update_user_settings(uid, {"context": patch})
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入用户配置失败: {error}") from error

    reload_model_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_user_agents(uid)
    return {"ok": True, "updated": sorted(patch), "current": _context_state(uid)}


# ============================================================
# 会话自动归档配置
# ============================================================


class ArchiveSettingsUpdate(BaseModel):
    """PUT /settings/archive 请求体；0 表示关闭自动归档。"""

    days: int = Field(..., ge=0, le=3650)


def _archive_state(user_id: str) -> dict[str, Any]:
    return user_data.archive_settings(user_id)


@router.get("/settings/archive")
async def get_archive_settings(
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """返回当前用户的自动归档天数配置。"""
    return _archive_state(effective_user_id(user_id))


@router.put("/settings/archive")
async def update_archive_settings(
    body: ArchiveSettingsUpdate,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """写入当前用户的归档配置，下一次进入对话页生效。"""
    uid = effective_user_id(user_id)
    try:
        user_data.update_user_settings(uid, {"archive": {"days": body.days}})
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入用户配置失败: {error}") from error
    return {"ok": True, "current": _archive_state(uid)}


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


def _sandbox_state(user_id: str) -> dict[str, Any]:
    """返回该用户的沙箱配置（密钥只回传是否已设置）。"""
    settings = user_data.sandbox_settings(user_id)
    configured = bool(settings["api_key"])
    notice = ""
    if not configured:
        notice = (
            "未配置沙箱服务，当前为项目工作区模式：Agent 只能读写项目文件，不能执行命令。"
        )
    return {
        "configured": configured,
        "mode": "sandbox" if configured else "local",
        "host": settings["host"],
        "port": settings["port"],
        "has_api_key": configured,
        "image": settings["image"],
        "default_timeout_seconds": settings["default_timeout_seconds"],
        "sandbox_timeout_seconds": settings["sandbox_timeout_seconds"],
        "prewarm_enabled": settings["prewarm_enabled"],
        "use_server_proxy": settings["use_server_proxy"],
        "notice": notice,
    }


@router.get("/settings/sandbox")
async def get_sandbox_settings(
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """返回当前用户的沙箱配置、执行模式、本机运行时探测与存储路径。"""
    from api.agent_loader import agent_loader

    uid = effective_user_id(user_id)
    local_env = detect_local_environment()
    return {
        "sandbox": _sandbox_state(uid),
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
            **user_data.user_paths(uid),
        },
    }


@router.get("/settings/sandbox/status")
async def get_sandbox_status() -> dict[str, Any]:
    """轻量返回执行模式，供对话页展示本地运行安全提示。"""
    from api.agent_loader import agent_loader

    return agent_loader.sandbox_status()


@router.put("/settings/sandbox")
async def update_sandbox_settings(
    body: SandboxSettingsUpdate,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """写入当前用户的沙箱配置；api_key 留空时保留原值。"""
    uid = effective_user_id(user_id)
    patch: dict[str, Any] = {}
    if body.host is not None:
        patch["host"] = body.host.strip() or "127.0.0.1"
    if body.port is not None:
        patch["port"] = body.port
    if body.clear_api_key:
        patch["api_key"] = ""
    elif body.api_key:
        patch["api_key"] = body.api_key.strip()
    if body.image is not None:
        patch["image"] = body.image.strip() or "myagent-sandbox:1"
    if body.default_timeout_seconds is not None:
        patch["default_timeout_seconds"] = body.default_timeout_seconds
    if body.sandbox_timeout_seconds is not None:
        patch["sandbox_timeout_seconds"] = body.sandbox_timeout_seconds
    if body.prewarm_enabled is not None:
        patch["prewarm_enabled"] = bool(body.prewarm_enabled)
    if body.use_server_proxy is not None:
        patch["use_server_proxy"] = bool(body.use_server_proxy)
    if not patch:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")

    try:
        user_data.update_user_settings(uid, {"sandbox": patch})
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入用户配置失败: {error}") from error

    reload_sandbox_config()
    from api.agent_loader import agent_loader

    await agent_loader.reset_user_agents(uid)
    return {
        "ok": True,
        "updated": sorted(patch),
        "sandbox": _sandbox_state(uid),
        "status": agent_loader.sandbox_status(),
    }


@router.post("/settings/sandbox/check")
async def check_sandbox_port(
    host: str | None = None,
    port: int | None = None,
    user_id: str = Query("", max_length=64),
) -> dict[str, Any]:
    """TCP 探测沙箱服务端口；只是连通性检查，不代表凭据有效。

    可传入前端表单中的 host/port，未传时使用当前用户已保存配置。
    """
    settings = user_data.sandbox_settings(effective_user_id(user_id))
    host = (host or "").strip() or settings["host"]
    port = int(port or settings["port"])
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
    if not settings["api_key"]:
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
