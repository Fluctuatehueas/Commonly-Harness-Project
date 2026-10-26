"""模型配置读写接口：支持 .env 热更新。

- GET  /settings/model  返回当前模型配置（API Key 只回传是否已设置，不回传值）
- PUT  /settings/model  更新 .env（api_key 留空则保留原值），并热更新运行中的模型
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import config as agent_config
from agent.config import PROJECT_ROOT, reload_model_config


router = APIRouter()

ENV_PATH = PROJECT_ROOT / ".env"
ENV_KEY_MODEL = "DEEPSEEK_MODEL_NAME"
ENV_KEY_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_KEY_API = "DEEPSEEK_API_KEY"
ENV_KEY_THINKING = "HARNESS_MODEL_THINKING"


class ModelSettingsUpdate(BaseModel):
    """PUT /settings/model 请求体；None 字段不改动，api_key 空串视为不改动。"""

    model_name: str | None = Field(None, min_length=1, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=300)
    thinking_enabled: bool | None = None


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
