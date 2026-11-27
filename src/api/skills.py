"""技能管理 HTTP 接口。

技能以磁盘目录为唯一事实源：

    <HARNESS_HOME>/skills/builtin/main/{skill}                 # 只读
    <HARNESS_HOME>/skills/builtin/subagents/{agent}/{skill}    # 只读
    <HARNESS_HOME>/skills/users/{uid}/main/{skill}
    <HARNESS_HOME>/skills/users/{uid}/subagents/{agent}/{skill}
    <HARNESS_HOME>/skills/users/{uid}/skills-disabled/

用户技能停用时移动到 ``skills-disabled``；内置技能按用户记录在
``skills-state.json`` 的 ``disabled_builtin`` 里，不移动文件。
删除是硬删除，仅对用户技能生效。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from agent import config as agent_config
from agent import file_index
from agent.tools.skill_tools import (
    MAIN_SKILLS_DIRECTORY,
    SKILL_NAME_PATTERN,
    SUBAGENTS_DIRECTORY,
    download_skill,
    ensure_skill_metadata,
    find_skill_directory,
    parse_skill_metadata,
    validate_archive_members,
)
from agent.user_data import (
    builtin_disabled_keys,
    builtin_skills_root,
    read_skills_state,
    write_skills_state,
)
from api.agent_loader import agent_loader


router = APIRouter(prefix="/skills", tags=["skills"])

logger = logging.getLogger(__name__)

PROTECTED_SKILL = "skill-management"
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_CHARS = 2_000_000
MAX_USER_SKILL_BYTES = 200 * 1024 * 1024
MAX_USER_SKILL_FILES = 2000
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# ============================================================
# 目录与元信息
# ============================================================


def _target_parts(target: str) -> tuple[str, ...]:
    """把前端 target 转成安全的相对目录片段。"""
    value = (target or MAIN_SKILLS_DIRECTORY).strip()
    if value == MAIN_SKILLS_DIRECTORY:
        return (MAIN_SKILLS_DIRECTORY,)
    parts = value.split("/")
    if (
        len(parts) == 2
        and parts[0] == SUBAGENTS_DIRECTORY
        and TARGET_PATTERN.fullmatch(parts[1])
    ):
        return (SUBAGENTS_DIRECTORY, parts[1])
    raise HTTPException(status_code=400, detail=f"非法技能归属: {target}")


def _validate_skill_name(name: str) -> str:
    """仅校验目录名格式，允许读取受保护技能。"""
    if not name or not SKILL_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="技能名只能包含字母、数字、连字符和下划线，且长度不超过 64",
        )
    return name


def _safe_skill_name(name: str) -> str:
    """校验技能目录名，并保护管理技能本身（仅用于写操作）。"""
    _validate_skill_name(name)
    if name.casefold() == PROTECTED_SKILL:
        raise HTTPException(status_code=400, detail="skill-management 是受保护的管理技能")
    return name


def _relative_parts(target: str, name: str) -> tuple[str, ...]:
    return (*_target_parts(target), _validate_skill_name(name))


def _roots(user_id: str | None = None) -> tuple[Path, Path]:
    """当前用户的（启用, 停用）技能根目录。

    技能库和停用目录都位于 ``HARNESS_HOME/skills/users/{user_id}/`` 下，
    不同用户的增删改互不可见。
    """
    from agent.identity import current_user_id
    from agent.user_data import (
        user_disabled_skills_root,
        user_skills_root,
    )

    uid = user_id or current_user_id()
    return user_skills_root(uid), user_disabled_skills_root(uid)


def _enabled_path(
    target: str, name: str, user_id: str | None = None
) -> Path:
    return _roots(user_id)[0].joinpath(*_relative_parts(target, name))


def _disabled_path(
    target: str, name: str, user_id: str | None = None
) -> Path:
    return _roots(user_id)[1].joinpath(*_relative_parts(target, name))


def _builtin_path(target: str, name: str) -> Path:
    return builtin_skills_root().joinpath(*_relative_parts(target, name))


def _read_frontmatter(directory: Path) -> dict[str, Any]:
    """读取 SKILL.md 的 YAML frontmatter；解析失败返回空。"""
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        return {}
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end < 0:
        return {}
    try:
        value = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def _skill_summary(directory: Path) -> tuple[str, str]:
    """优先 metadata.json，缺失时回退 SKILL.md frontmatter。"""
    name = directory.name
    description = ""
    metadata_file = directory / "metadata.json"
    if metadata_file.is_file():
        try:
            value = json.loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                name = str(value.get("name") or name)
                description = str(value.get("description") or "")
                return name, description
        except (OSError, json.JSONDecodeError):
            pass
    frontmatter = _read_frontmatter(directory)
    name = str(frontmatter.get("name") or name)
    description = str(frontmatter.get("description") or "")
    return name, description


def _directory_stats(directory: Path) -> tuple[int, int, str]:
    """返回 (文件数, 字节数, 最近修改时间)。"""
    files = 0
    size = 0
    latest = 0.0
    for root, directories, filenames in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".pyc"):
                continue
            path = Path(root) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            files += 1
            size += stat.st_size
            latest = max(latest, stat.st_mtime)
    if latest <= 0:
        try:
            latest = directory.stat().st_mtime
        except OSError:
            latest = 0
    updated = (
        datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
        if latest
        else datetime.now(timezone.utc).isoformat()
    )
    return files, size, updated


def _item(
    target: str,
    name: str,
    directory: Path,
    enabled: bool,
    *,
    source: str = "user",
) -> dict[str, Any]:
    title, description = _skill_summary(directory)
    file_count, size_bytes, updated_at = _directory_stats(directory)
    return {
        "skill_id": f"{target}:{name}",
        "name": name,
        "title": title,
        "description": description,
        "target": target,
        "enabled": enabled,
        "source": source,
        "editable": source == "user",
        "deletable": source == "user" and name.casefold() != PROTECTED_SKILL,
        "protected": name.casefold() == PROTECTED_SKILL,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "updated_at": updated_at,
        "path": str(directory),
    }


def _collect(
    root: Path,
    enabled: bool,
    *,
    source: str = "user",
) -> list[dict[str, Any]]:
    """扫描一个技能根目录下的 main 与 subagents。"""
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    main_dir = root / MAIN_SKILLS_DIRECTORY
    if main_dir.is_dir():
        for directory in main_dir.iterdir():
            if directory.is_dir() and (directory / "SKILL.md").is_file():
                items.append(
                    _item(
                        MAIN_SKILLS_DIRECTORY,
                        directory.name,
                        directory,
                        enabled,
                        source=source,
                    )
                )
    subagents_dir = root / SUBAGENTS_DIRECTORY
    if subagents_dir.is_dir():
        for agent_dir in subagents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            target = f"{SUBAGENTS_DIRECTORY}/{agent_dir.name}"
            for directory in agent_dir.iterdir():
                if directory.is_dir() and (directory / "SKILL.md").is_file():
                    items.append(
                        _item(
                            target,
                            directory.name,
                            directory,
                            enabled,
                            source=source,
                        )
                    )
    return items


def _scan_all(user_id: str) -> list[dict[str, Any]]:
    """合并用户技能与内置技能：用户同名技能覆盖内置。"""
    user_root, disabled_root = _roots(user_id)
    enabled_items = _collect(user_root, True, source="user")
    disabled_items = _collect(disabled_root, False, source="user")
    builtin_items = _collect(builtin_skills_root(), True, source="builtin")

    user_keys = {
        (item["target"], item["name"])
        for item in [*enabled_items, *disabled_items]
    }
    disabled_builtin = builtin_disabled_keys(user_id)
    merged_builtin: list[dict[str, Any]] = []
    for item in builtin_items:
        if (item["target"], item["name"]) in user_keys:
            continue
        key = f"{item['target']}:{item['name']}"
        item["enabled"] = key not in disabled_builtin
        merged_builtin.append(item)

    merged = enabled_items + disabled_items + merged_builtin
    merged.sort(key=lambda item: (item["target"], item["title"].casefold(), item["name"]))
    return merged


def _list_all() -> list[dict[str, Any]]:
    """通过文件索引读取技能列表；索引失效时自动重建。"""
    from agent.identity import current_user_id

    uid = current_user_id()
    user_root, disabled_root = _roots(uid)
    roots = [
        user_root,
        disabled_root,
        builtin_skills_root(),
        user_root / "skills-state.json",
    ]
    return file_index.get(
        "skills",
        uid,
        roots,
        lambda: _scan_all(uid),
    )


def _locate(
    target: str,
    name: str,
    user_id: str | None = None,
) -> tuple[Path, bool, str]:
    """返回技能目录、是否启用、来源（user / builtin）。"""
    from agent.identity import current_user_id

    uid = user_id or current_user_id()
    enabled = _enabled_path(target, name, uid)
    if enabled.is_dir():
        return enabled, True, "user"
    disabled = _disabled_path(target, name, uid)
    if disabled.is_dir():
        return disabled, False, "user"
    builtin = _builtin_path(target, name)
    if builtin.is_dir():
        return builtin, f"{target}:{name}" not in builtin_disabled_keys(uid), "builtin"
    raise HTTPException(status_code=404, detail="技能不存在")


def _remove_directory(path: Path) -> None:
    """硬删除目录。

    Windows 上新写入的文件可能被杀软/索引短暂占用，rmtree 偶发
    "目录不是空的"；这里重试两次，仍失败才向调用方抛出。
    """
    last_error: OSError | None = None
    for _ in range(3):
        try:
            shutil.rmtree(path)
            return
        except OSError as error:
            last_error = error
            time.sleep(0.15)
    if last_error is not None:
        raise last_error


async def _refresh_agents() -> None:
    """技能目录变化后清空图缓存，让下一轮对话重新发现技能。"""
    await agent_loader.reset_agents()


def _usage(root: Path) -> tuple[int, int]:
    """返回 (文件数, 字节数)，忽略 __pycache__ 与 .pyc。"""
    files = 0
    size = 0
    if not root.is_dir():
        return files, size
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".pyc"):
                continue
            try:
                stat = (Path(dirpath) / filename).stat()
            except OSError:
                continue
            files += 1
            size += stat.st_size
    return files, size


def _enforce_quota(user_root: Path, cleanup: Path | None = None) -> None:
    """用户技能库配额检查；超限时可清理本次刚安装的目录。"""
    files, size = _usage(user_root)
    if size <= MAX_USER_SKILL_BYTES and files <= MAX_USER_SKILL_FILES:
        return
    if cleanup is not None and cleanup.is_dir():
        try:
            _remove_directory(cleanup)
        except OSError:
            logger.warning("清理超配额技能失败：%s", cleanup, exc_info=True)
    raise HTTPException(
        status_code=413,
        detail=(
            f"用户技能库超过配额（{size // (1024 * 1024)} MB / {files} 个文件），"
            f"上限 {MAX_USER_SKILL_BYTES // (1024 * 1024)} MB / {MAX_USER_SKILL_FILES} 个文件"
        ),
    )


def _move_skill(target: str, name: str, *, enabled: bool) -> dict[str, Any]:
    """在启用/停用目录之间移动技能；同名冲突时拒绝覆盖。"""
    source = _disabled_path(target, name) if enabled else _enabled_path(target, name)
    destination = _enabled_path(target, name) if enabled else _disabled_path(target, name)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail="技能不存在")
    if destination.exists():
        raise HTTPException(status_code=409, detail="目标目录已存在同名技能")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return _item(target, name, destination, enabled)


# ============================================================
# 安装任务（异步进度）
# ============================================================

# 内存任务表：前端确认后立即返回 job_id，后台线程继续下载/解压。
_INSTALL_JOBS: dict[str, dict[str, Any]] = {}
_INSTALL_TASKS: dict[str, asyncio.Task[Any]] = {}
JOB_TTL_SECONDS = 30 * 60
MAX_JOBS = 40

# 阶段 → 进度百分比，前端据此画进度条。
STAGE_PROGRESS = {
    "queued": 0,
    "downloading": 25,
    "validating": 55,
    "extracting": 65,
    "installing": 80,
    "refreshing": 92,
    "done": 100,
    "error": 100,
}


def _prune_jobs() -> None:
    """只保留最近 40 个任务，并清理超过 30 分钟的终态任务。"""
    now = time.time()
    finished = [
        (job_id, job)
        for job_id, job in _INSTALL_JOBS.items()
        if job["status"] in {"done", "error"}
    ]
    for job_id, job in finished:
        if now - float(job["_updated_ts"]) > JOB_TTL_SECONDS:
            _INSTALL_JOBS.pop(job_id, None)
            _INSTALL_TASKS.pop(job_id, None)
    overflow = len(_INSTALL_JOBS) - MAX_JOBS
    if overflow > 0:
        oldest = sorted(
            _INSTALL_JOBS.items(),
            key=lambda item: float(item[1]["_updated_ts"]),
        )[:overflow]
        for job_id, _ in oldest:
            if _INSTALL_JOBS[job_id]["status"] in {"done", "error"}:
                _INSTALL_JOBS.pop(job_id, None)
                _INSTALL_TASKS.pop(job_id, None)


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    """去掉内部字段后返回给前端。"""
    return {key: value for key, value in job.items() if not key.startswith("_")}


def _create_job(kind: str, label: str) -> dict[str, Any]:
    from agent.identity import current_user_id

    _prune_jobs()
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    job: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "排队中",
        "error": "",
        "skill": None,
        "created_at": now,
        "updated_at": now,
        "_updated_ts": time.time(),
        # 安装任务按用户隔离：任务列表只回传当前用户发起的任务。
        "_user_id": current_user_id(),
    }
    _INSTALL_JOBS[job_id] = job
    return job


def _update_job(job_id: str, **fields: Any) -> None:
    job = _INSTALL_JOBS.get(job_id)
    if job is None:
        return
    stage = fields.get("stage")
    if stage and "progress" not in fields:
        fields["progress"] = STAGE_PROGRESS.get(str(stage), job["progress"])
    job.update(fields)
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    job["_updated_ts"] = time.time()


def _fail_job(job_id: str, message: str) -> None:
    _update_job(
        job_id,
        status="error",
        stage="error",
        message=message,
        error=message,
    )


async def _run_download_job(job_id: str, url: str) -> None:
    """后台执行 URL 技能安装，并把阶段写回任务表。"""
    from agent.identity import current_user_id

    _update_job(job_id, status="running", stage="downloading", message="正在下载技能包")
    try:
        installed = await asyncio.to_thread(download_skill, _roots()[0], url)
        _update_job(job_id, stage="validating", message="正在校验技能元信息")
        name = str(installed.get("skill_name") or "")
        try:
            directory, enabled, _source = _locate(MAIN_SKILLS_DIRECTORY, name)
        except HTTPException as error:
            raise RuntimeError("技能已下载但无法定位安装目录") from error
        _enforce_quota(_roots()[0], cleanup=directory)
        file_index.invalidate("skills", current_user_id())
        _update_job(job_id, stage="refreshing", message="正在刷新 Agent 技能")
        await _refresh_agents()
        item = _item(MAIN_SKILLS_DIRECTORY, name, directory, enabled)
        _update_job(
            job_id,
            status="done",
            stage="done",
            message="安装完成",
            skill=item,
        )
    except ValueError as error:
        _fail_job(job_id, str(error))
    except OSError as error:
        _fail_job(job_id, f"技能下载失败: {error}")
    except Exception as error:  # noqa: BLE001 - 后台任务必须把失败写回任务表
        _fail_job(job_id, f"安装失败: {error}")


async def _run_archive_job(job_id: str, data: bytes, fallback: str) -> None:
    """后台执行 ZIP 技能包安装。"""
    from agent.identity import current_user_id

    _update_job(job_id, status="running", stage="extracting", message="正在解压技能包")
    try:
        root, disabled_root = _roots()
        item = await asyncio.to_thread(
            _install_archive,
            root,
            data,
            fallback_name=fallback,
            disabled_root=disabled_root,
        )
        _enforce_quota(root, cleanup=Path(item["path"]))
        file_index.invalidate("skills", current_user_id())
        _update_job(job_id, stage="refreshing", message="正在刷新 Agent 技能")
        await _refresh_agents()
        _update_job(
            job_id,
            status="done",
            stage="done",
            message="安装完成",
            skill=item,
        )
    except HTTPException as error:
        _fail_job(job_id, str(error.detail))
    except (zipfile.BadZipFile, ValueError) as error:
        _fail_job(job_id, str(error))
    except OSError as error:
        _fail_job(job_id, f"安装技能失败: {error}")
    except Exception as error:  # noqa: BLE001
        _fail_job(job_id, f"安装失败: {error}")


def _start_task(job: dict[str, Any], coroutine: Any) -> None:
    task = asyncio.create_task(coroutine, name=f"skill-install-{job['job_id']}")
    _INSTALL_TASKS[job["job_id"]] = task


@router.get("/jobs")
async def list_install_jobs() -> dict[str, Any]:
    """返回当前用户最近的安装任务，供页面刷新后恢复底部进度卡。"""
    from agent.identity import current_user_id

    _prune_jobs()
    uid = current_user_id()
    jobs = sorted(
        (job for job in _INSTALL_JOBS.values() if job.get("_user_id") == uid),
        key=lambda item: float(item["_updated_ts"]),
        reverse=True,
    )
    return {"jobs": [_job_public(job) for job in jobs[:MAX_JOBS]]}


@router.get("/jobs/{job_id}")
async def get_install_job(job_id: str) -> dict[str, Any]:
    from agent.identity import current_user_id

    job = _INSTALL_JOBS.get(job_id)
    if job is None or job.get("_user_id") != current_user_id():
        raise HTTPException(status_code=404, detail="安装任务不存在或已过期")
    return _job_public(job)


# ============================================================
# 请求模型
# ============================================================


class SkillCreateRequest(BaseModel):
    """手动编写技能。"""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=500)
    content: str = Field("", max_length=200_000)


class SkillDownloadRequest(BaseModel):
    """从 URL 安装技能。"""

    url: str = Field(..., min_length=8, max_length=1000)


class SkillToggleRequest(BaseModel):
    target: str = Field(MAIN_SKILLS_DIRECTORY, max_length=80)
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool


class SkillTargetRequest(BaseModel):
    target: str = Field(MAIN_SKILLS_DIRECTORY, max_length=80)
    name: str = Field(..., min_length=1, max_length=64)


# ============================================================
# 查询
# ============================================================


@router.get("/storage")
async def get_storage() -> dict[str, Any]:
    """返回当前用户技能库真实落盘位置与安装限制。"""
    root, disabled_root = _roots()
    return {
        "harness_home": str(agent_config.HARNESS_HOME),
        "skills_root": str(root),
        "main_root": str(root / MAIN_SKILLS_DIRECTORY),
        "builtin_root": str(builtin_skills_root()),
        "disabled_root": str(disabled_root),
        "max_package_mb": MAX_PACKAGE_BYTES // (1024 * 1024),
        "max_user_mb": MAX_USER_SKILL_BYTES // (1024 * 1024),
        "max_user_files": MAX_USER_SKILL_FILES,
    }


@router.get("")
async def list_skills(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    keyword: str = Query("", max_length=200),
    enabled: bool | None = Query(None),
    target: str = Query("", max_length=80),
) -> dict[str, Any]:
    """分页返回技能列表，支持名称/描述搜索与启用状态筛选。"""
    items = _list_all()
    needle = keyword.strip().casefold()
    if needle:
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    item["name"],
                    item["title"],
                    item["description"],
                    item["target"],
                ]
            ).casefold()
        ]
    if enabled is not None:
        items = [item for item in items if item["enabled"] is enabled]
    if target:
        items = [item for item in items if item["target"] == target]

    total = len(items)
    pages = max((total + page_size - 1) // page_size, 1)
    current = min(page, pages)
    start = (current - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": current,
        "page_size": page_size,
        "pages": pages,
    }


def _strip_frontmatter(text: str) -> str:
    """展示用正文：去掉 SKILL.md 顶部的 YAML frontmatter。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end < 0:
        return text
    return "\n".join(lines[end + 1 :]).lstrip("\n")


@router.get("/content")
async def get_skill_content(
    target: str = Query(MAIN_SKILLS_DIRECTORY, max_length=80),
    name: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    """读取技能详情：SKILL.md 正文 + 元信息。"""
    directory, enabled, source = _locate(target, name)
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        raise HTTPException(status_code=404, detail="技能缺少 SKILL.md")
    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"读取技能失败: {error}") from error
    content = _strip_frontmatter(raw)
    truncated = len(content) > MAX_MARKDOWN_CHARS
    if truncated:
        content = content[:MAX_MARKDOWN_CHARS]
    return {
        "skill": _item(target, name, directory, enabled, source=source),
        "content": content,
        "truncated": truncated,
    }


# ============================================================
# 变更
# ============================================================


@router.post("/toggle")
async def toggle_skill(body: SkillToggleRequest) -> dict[str, Any]:
    """启用或停用技能；内置技能写用户级停用列表，用户技能移动目录。"""
    from agent.identity import current_user_id

    _validate_skill_name(body.name)
    uid = current_user_id()
    directory, current, source = _locate(body.target, body.name, uid)

    if source == "builtin":
        if current == body.enabled:
            return _item(body.target, body.name, directory, current, source="builtin")
        state = read_skills_state(uid)
        disabled = set(state["disabled_builtin"])
        key = f"{body.target}:{body.name}"
        if body.enabled:
            disabled.discard(key)
        else:
            disabled.add(key)
        write_skills_state(
            uid,
            {
                "builtin_version": state["builtin_version"] or "1.1",
                "disabled_builtin": sorted(disabled),
            },
        )
        file_index.invalidate("skills", uid)
        await _refresh_agents()
        return _item(body.target, body.name, directory, body.enabled, source="builtin")

    if current == body.enabled:
        return _item(body.target, body.name, directory, current, source=source)
    item = await asyncio.to_thread(_move_skill, body.target, body.name, enabled=body.enabled)
    file_index.invalidate("skills", uid)
    await _refresh_agents()
    return item


@router.delete("/item")
async def delete_skill(
    target: str = Query(MAIN_SKILLS_DIRECTORY, max_length=80),
    name: str = Query(..., min_length=1, max_length=64),
) -> dict[str, bool]:
    """硬删除用户技能；内置技能只能停用，不能删除。"""
    from agent.identity import current_user_id

    directory, _enabled, source = _locate(target, name)
    if source == "builtin":
        raise HTTPException(status_code=400, detail="内置技能不可删除，请使用停用")
    _safe_skill_name(name)
    removed = False
    for path in (_enabled_path(target, name), _disabled_path(target, name)):
        if path.is_dir():
            try:
                _remove_directory(path)
            except OSError as error:
                raise HTTPException(status_code=500, detail=f"删除技能失败: {error}") from error
            removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="技能不存在")
    file_index.invalidate("skills", current_user_id())
    await _refresh_agents()
    return {"ok": True}


@router.post("/create")
async def create_skill(body: SkillCreateRequest) -> dict[str, Any]:
    """手动创建一个主 Agent 技能。"""
    name = _safe_skill_name(body.name.strip())
    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="技能描述不能为空")
    directory = _enabled_path(MAIN_SKILLS_DIRECTORY, name)
    if directory.exists() or _disabled_path(MAIN_SKILLS_DIRECTORY, name).exists():
        raise HTTPException(status_code=409, detail=f"技能 '{name}' 已存在")

    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    skill_text = f"---\n{frontmatter}\n---\n\n# {name}\n\n{body.content.strip()}\n"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "SKILL.md").write_text(skill_text, encoding="utf-8")
        (directory / "metadata.json").write_text(
            json.dumps({"name": name, "description": description}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        if directory.exists():
            try:
                _remove_directory(directory)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=f"创建技能失败: {error}") from error

    from agent.identity import current_user_id

    _enforce_quota(_roots()[0], cleanup=directory)
    file_index.invalidate("skills", current_user_id())
    await _refresh_agents()
    return _item(MAIN_SKILLS_DIRECTORY, name, directory, True)


def _install_archive(
    skills_root: Path,
    data: bytes,
    *,
    fallback_name: str,
    disabled_root: Path | None = None,
) -> dict[str, Any]:
    """校验、解压并安装一个 ZIP 技能包到当前用户的主 Agent 目录。"""
    main_directory = skills_root / MAIN_SKILLS_DIRECTORY
    main_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=main_directory) as temporary_name:
        temporary_directory = Path(temporary_name)
        archive_path = temporary_directory / "skill.zip"
        archive_path.write_bytes(data)
        with zipfile.ZipFile(archive_path) as archive:
            validate_archive_members(archive)
            extract_directory = temporary_directory / "extracted"
            archive.extractall(extract_directory)

        source_directory = find_skill_directory(extract_directory, None)
        ensure_skill_metadata(source_directory)
        metadata = parse_skill_metadata(source_directory)
        name = _safe_skill_name(str(metadata.get("name") or fallback_name))
        target_directory = main_directory / name
        disabled_root = disabled_root or _roots()[1]
        if target_directory.exists() or (disabled_root / MAIN_SKILLS_DIRECTORY / name).exists():
            raise HTTPException(status_code=409, detail=f"技能 '{name}' 已存在")
        shutil.move(str(source_directory), str(target_directory))
        return _item(
            MAIN_SKILLS_DIRECTORY,
            name,
            target_directory,
            True,
        )


@router.post("/upload", status_code=202)
async def upload_skill(
    file: UploadFile = File(...),
    target: str = Form(MAIN_SKILLS_DIRECTORY),
) -> dict[str, Any]:
    """上传 ZIP 技能包：立即返回任务 ID，后台解压并安装。"""
    if _target_parts(target) != (MAIN_SKILLS_DIRECTORY,):
        raise HTTPException(status_code=400, detail="上传技能仅支持安装到主 Agent")
    data = await file.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail="技能包超过 32 MB 限制")
    filename = file.filename or "skill.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="只支持 ZIP 技能包")
    fallback = Path(filename).stem
    job = _create_job("zip", filename)
    _start_task(
        job,
        _run_archive_job(job["job_id"], data, fallback),
    )
    return {"job_id": job["job_id"], "job": _job_public(job)}


@router.post("/download-url", status_code=202)
async def download_skill_from_url(body: SkillDownloadRequest) -> dict[str, Any]:
    """下载 ZIP 或 GitHub 技能目录：立即返回任务 ID，后台安装。"""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="技能链接必须以 http(s) 开头")
    job = _create_job("url", url)
    _start_task(
        job,
        _run_download_job(job["job_id"], url),
    )
    return {"job_id": job["job_id"], "job": _job_public(job)}


@router.post("/reveal")
async def reveal_skill(body: SkillTargetRequest) -> dict[str, bool]:
    """在服务器所在机器的资源管理器中打开技能目录。"""
    directory, _, _source = _locate(body.target, body.name)
    path = str(directory)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"无法打开资源管理器: {error}") from error
    return {"ok": True}
