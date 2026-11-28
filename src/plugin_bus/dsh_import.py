"""DSH 条目转译为本地技能。

DSH 的 Cordis 插件不能直接运行，但部分 npm 包内含 ``SKILL.md``。这里把这类
条目下载、校验、解压，取出技能目录后复用技能安装链路写入当前用户的技能库。
整个过程是异步 job，失败只影响单条转译，不影响插件系统本身。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx

from plugin_bus import catalog_dsh, jobs
from plugin_bus.errors import PluginError, PluginPackageInvalid

logger = logging.getLogger(__name__)

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_SKILL_DEPTH = 2


def _config() -> Any:
    from agent import config

    return config


def verify_integrity(data: bytes, *, integrity: str = "", shasum: str = "") -> None:
    """校验 npm dist 完整性；有 integrity 用 sha512，否则回退 shasum(sha1)。"""
    value = str(integrity or "").strip()
    if value.startswith("sha512-"):
        try:
            expected = base64.b64decode(value.split("-", 1)[1])
        except (ValueError, IndexError) as error:
            raise PluginPackageInvalid("DSH 包完整性字段无法解析") from error
        if hashlib.sha512(data).digest() != expected:
            raise PluginPackageInvalid("DSH 包 sha512 校验失败")
        return
    if shasum:
        if hashlib.sha1(data).hexdigest().lower() != str(shasum).lower():
            raise PluginPackageInvalid("DSH 包 sha1 校验失败")


def extract_tarball(data: bytes, target: Path) -> None:
    """安全解压 npm tgz：拒绝绝对路径、路径穿越与链接。"""
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise PluginPackageInvalid("DSH 包文件数超过限制")
        total = sum(member.size for member in members if member.isfile())
        if total > MAX_PACKAGE_BYTES:
            raise PluginPackageInvalid("DSH 包解压体积超过限制")
        for member in members:
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ":" in name or ".." in Path(name).parts:
                raise PluginPackageInvalid("DSH 包包含不安全的路径")
            if member.issym() or member.islnk():
                raise PluginPackageInvalid("DSH 包不能包含符号链接")
            if not (member.isfile() or member.isdir()):
                raise PluginPackageInvalid("DSH 包包含不支持的文件类型")
        try:
            archive.extractall(target, members=members, filter="data")
        except TypeError:  # Python < 3.12 没有 filter 参数
            archive.extractall(target, members=members)


def strip_wrapper(root: Path) -> Path:
    """npm tarball 统一有 ``package/`` 包装层；单目录时下钻一层。"""
    children = list(root.iterdir())
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return root


def locate_skill_directory(root: Path) -> Path | None:
    """在解压目录里找唯一包含 SKILL.md 的技能目录（最多下探两层）。"""
    candidates: list[Path] = []
    for current, directories, files in os.walk(root):
        directory = Path(current)
        depth = len(directory.relative_to(root).parts)
        if depth >= MAX_SKILL_DEPTH:
            directories[:] = []
        if "SKILL.md" in files:
            candidates.append(directory)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (len(item.relative_to(root).parts), str(item)))
    return candidates[0]


def pack_directory(directory: Path) -> bytes:
    """把技能目录打成 ZIP，复用技能安装通道。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False
    ) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


async def _download(
    url: str, *, max_bytes: int, client: httpx.AsyncClient | None = None
) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise PluginPackageInvalid("DSH 包地址必须以 http(s) 开头")
    if client is not None:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise PluginPackageInvalid("DSH 包超过体积限制")
        return response.content
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as session:
        async with session.stream("GET", url) as response:
            if response.status_code >= 400:
                raise PluginPackageInvalid(
                    f"下载 DSH 包失败：HTTP {response.status_code}"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PluginPackageInvalid("DSH 包超过体积限制")
                chunks.append(chunk)
    return b"".join(chunks)


def _install_bundle(user_id: str, bundle: bytes, entry: dict[str, Any]) -> dict[str, Any]:
    """复用技能安装实现落盘，返回技能条目。"""
    from api import skills as skills_api
    from agent.user_data import user_disabled_skills_root, user_skills_root

    root = user_skills_root(user_id)
    disabled_root = user_disabled_skills_root(user_id)
    fallback = catalog_dsh.normalize_plugin_id(
        str(entry.get("npm_name") or entry.get("plugin_id") or "dsh-skill")
    )
    item = skills_api._install_archive(  # noqa: SLF001 - 复用同一套校验与落盘
        root, bundle, fallback_name=fallback, disabled_root=disabled_root
    )
    skills_api._enforce_quota(root, cleanup=Path(item["path"]))  # noqa: SLF001
    return item


async def _refresh_skills(user_id: str) -> None:
    try:
        from agent import file_index

        file_index.invalidate("skills", user_id)
    except Exception:
        logger.debug("技能索引失效失败", exc_info=True)
    try:
        from api.agent_loader import agent_loader

        await agent_loader.reset_agents()
    except Exception:
        logger.debug("重置 Agent 图失败", exc_info=True)


async def _audit(user_id: str, entry: dict[str, Any], *, result: str, detail: dict[str, Any]) -> None:
    try:
        from plugin_bus.store import PluginStore

        await PluginStore().audit(
            user_id=user_id,
            plugin_id=str(entry.get("plugin_id") or ""),
            version=str(entry.get("version") or ""),
            action="import",
            result=result,
            detail=detail,
        )
    except Exception:
        logger.debug("DSH 转译审计写入失败", exc_info=True)


async def import_skill(
    user_id: str,
    entry: dict[str, Any],
    *,
    job_id: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """把一条 DSH 市场条目转译成当前用户的技能。"""
    if str(entry.get("source") or "") != catalog_dsh.DSH_SOURCE:
        raise PluginPackageInvalid("该条目不是 DSH 来源")
    if entry.get("import_kind") != catalog_dsh.IMPORT_KIND_SKILL:
        raise PluginPackageInvalid(
            str(entry.get("unsupported_reason") or "该条目不支持转译为技能")
        )
    npm_dist = entry.get("npm_dist") if isinstance(entry.get("npm_dist"), dict) else {}
    tarball = str(npm_dist.get("tarball") or "").strip()
    if not tarball:
        raise PluginPackageInvalid("该条目缺少 npm 包地址，请刷新目录后重试")

    if job_id:
        jobs.update_job(
            job_id, status="running", stage="downloading", message="正在下载 DSH 包"
        )
    data = await _download(tarball, max_bytes=MAX_PACKAGE_BYTES, client=client)
    verify_integrity(
        data,
        integrity=str(npm_dist.get("integrity") or ""),
        shasum=str(npm_dist.get("shasum") or ""),
    )

    if job_id:
        jobs.update_job(job_id, stage="extracting", message="正在解压并查找 SKILL.md")
    temporary_root = Path(_config().PLUGIN_ROOT) / "users" / user_id / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=temporary_root) as temp_name:
            extract_root = Path(temp_name) / "extracted"
            await asyncio.to_thread(extract_tarball, data, extract_root)
            skill_dir = locate_skill_directory(strip_wrapper(extract_root))
            if skill_dir is None:
                raise PluginPackageInvalid("该 DSH 包未包含 SKILL.md，无法转译为技能")
            bundle = await asyncio.to_thread(pack_directory, skill_dir)
            if job_id:
                jobs.update_job(
                    job_id, stage="installing", message="正在安装到用户技能库"
                )
            item = await asyncio.to_thread(_install_bundle, user_id, bundle, entry)
    except Exception:
        await _audit(
            user_id,
            entry,
            result="failed",
            detail={"stage": "install"},
        )
        raise

    if job_id:
        jobs.update_job(job_id, stage="refreshing", message="正在刷新技能列表")
    await _refresh_skills(user_id)
    await _audit(
        user_id,
        entry,
        result="ok",
        detail={
            "skill": item.get("name"),
            "skill_path": item.get("path"),
            "npm_name": entry.get("npm_name"),
        },
    )
    return {
        "skill": item,
        "npm_name": entry.get("npm_name"),
        "version": entry.get("version"),
    }


__all__ = [
    "MAX_PACKAGE_BYTES",
    "verify_integrity",
    "extract_tarball",
    "strip_wrapper",
    "locate_skill_directory",
    "pack_directory",
    "import_skill",
]
