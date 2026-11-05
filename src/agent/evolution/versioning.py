"""生成一次 Agent run 的版本快照。

没有版本快照的轨迹只能做统计，不能做反事实评估。这里尽量从现有配置与
文件内容推导，缺少来源时使用稳定空值，而不是伪造版本号。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from agent.config import (
    DEEPSEEK_MODEL_NAME,
    EVOLUTION_BUILD_VERSION,
    PROJECT_ROOT,
)
from agent.evolution.types import VersionManifest

_PROMPT_CACHE: tuple[str, str] | None = None


def _file_hash(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(content).hexdigest()[:8]


def _prompt_version() -> str:
    global _PROMPT_CACHE
    path = PROJECT_ROOT / "src" / "agent" / "memory" / "prompts.py"
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        modified = 0
    cache_key = f"{path}:{modified}"
    if _PROMPT_CACHE and _PROMPT_CACHE[0] == cache_key:
        return _PROMPT_CACHE[1]
    version = _file_hash(path) or "dev"
    _PROMPT_CACHE = (cache_key, version)
    return version


def _tool_schema_version() -> str:
    try:
        from agent.mcp.runtime import mcp_runtime

        generation = int(getattr(mcp_runtime.registry, "generation", 0) or 0)
        return f"mcp_gen_{generation}"
    except Exception:
        return "unknown"


def _config_hash() -> str:
    parts = [
        DEEPSEEK_MODEL_NAME,
        os.getenv("HARNESS_MODEL_THINKING", ""),
        os.getenv("HARNESS_MODEL_CONTEXT_LIMIT", ""),
        os.getenv("HARNESS_WEB_SEARCH", ""),
        os.getenv("HARNESS_HOST_ACCESS_ENABLED", ""),
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


def _skill_generation() -> int:
    raw = (os.getenv("HARNESS_SKILL_GENERATION") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 0


def build_version_manifest() -> VersionManifest:
    """构造当前进程的版本快照；函数内不抛异常。"""
    return VersionManifest(
        harness_version=EVOLUTION_BUILD_VERSION or "dev",
        prompt_version=_prompt_version(),
        skill_generation=_skill_generation(),
        tool_schema_version=_tool_schema_version(),
        model_name=DEEPSEEK_MODEL_NAME,
        retrieval_snapshot=(os.getenv("HARNESS_RAG_SNAPSHOT") or "").strip(),
        config_hash=_config_hash(),
    )


def manifest_to_dict(manifest: VersionManifest) -> dict[str, Any]:
    return manifest.to_dict()
