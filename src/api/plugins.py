"""插件管理 REST API。

接口只做参数校验与任务编排：安装/启停/更新/卸载全部走 Plugin Bus，
市场不直接改磁盘、不自己起进程。
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.identity import current_user_id
from plugin_bus import installer, jobs, storage
from plugin_bus.errors import PluginError, PluginNotFound
from plugin_bus.runtime import get_plugin_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _config() -> Any:
    from agent import config

    return config


def _http(error: PluginError) -> HTTPException:
    return HTTPException(status_code=error.http_status, detail=error.to_dict())


def _runtime():
    return get_plugin_runtime(current_user_id())


def _frame_ancestors() -> str:
    """允许控制台（含本地 Vite / Electron）嵌入插件 UI。"""
    raw = (os.getenv("HARNESS_CORS_ORIGINS") or "").strip()
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    return " ".join(["'self'", *origins])


async def _catalog(force: bool = False) -> Any:
    from plugin_bus.catalog import get_catalog_service

    return await get_catalog_service().get(force=force)


async def _catalog_entry(
    plugin_id: str, version: str = "", *, force: bool = False
) -> dict[str, Any] | None:
    from plugin_bus.catalog import get_catalog_service

    return await get_catalog_service().find(plugin_id, version, force=force)


def _find_installed(runtime: Any, plugin_id: str) -> Any:
    installed = runtime.installed_map().get(plugin_id)
    if installed is None:
        raise PluginNotFound(plugin_id)
    return installed


async def _sandbox_backend(user_id: str) -> Any | None:
    try:
        from api.agent_loader import agent_loader

        return await agent_loader.get_sandbox_backend(user_id)
    except Exception:
        logger.warning("获取插件安装沙箱失败", exc_info=True)
        return None


# ============================================================
# 请求模型
# ============================================================


class InstallRequest(BaseModel):
    source: Literal["market", "url", "remote"] = "market"
    plugin_id: str = Field("", max_length=64)
    version: str = Field("", max_length=32)
    url: str = Field("", max_length=2000)
    sha256: str = Field("", max_length=128)
    manifest: dict[str, Any] | None = None
    auto_enable: bool = True


class ToggleRequest(BaseModel):
    enabled: bool


class ConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str = Field("", max_length=256)


class UpdateRequest(BaseModel):
    version: str = Field("", max_length=32)


# ============================================================
# 市场
# ============================================================


@router.get("/market/catalog")
async def market_catalog(
    keyword: str = Query("", max_length=200),
    capability: str = Query("", max_length=32),
    source: str = Query("", max_length=32),
    sort: Literal["popular", "latest", "name"] = "popular",
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    catalog = await _catalog(force=refresh)
    items = list(catalog.items)
    needle = keyword.strip().casefold()
    if needle:
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    str(item.get("plugin_id") or ""),
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                    " ".join(str(tag) for tag in item.get("tag") or []),
                ]
            ).casefold()
        ]
    if capability:
        items = [
            item
            for item in items
            if capability in (item.get("capabilities") or [])
        ]
    if source:
        items = [item for item in items if str(item.get("source") or "") == source]
    if sort == "latest":
        items.sort(key=lambda item: str(item.get("version") or ""), reverse=True)
    elif sort == "name":
        items.sort(key=lambda item: str(item.get("name") or "").casefold())
    else:
        items.sort(
            key=lambda item: int(item.get("downloadCount") or item.get("like") or 0),
            reverse=True,
        )
    runtime = _runtime()
    installed = {item["plugin_id"]: item for item in await runtime.list_installed()}
    for item in items:
        current = installed.get(str(item.get("plugin_id") or ""))
        item["installed"] = bool(current)
        item["installed_version"] = current["version"] if current else ""
        item["enabled"] = bool(current["enabled"]) if current else False
    total = len(items)
    pages = max((total + page_size - 1) // page_size, 1)
    current_page = min(page, pages)
    start = (current_page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": current_page,
        "page_size": page_size,
        "pages": pages,
        "stale": catalog.stale,
        "fetched_at": catalog.fetched_at,
        "sources": catalog.sources,
    }


@router.get("/market/{plugin_id}")
async def market_detail(plugin_id: str) -> dict[str, Any]:
    entry = await _catalog_entry(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="市场中没有该插件")
    runtime = _runtime()
    installed = {item["plugin_id"]: item for item in await runtime.list_installed()}
    current = installed.get(plugin_id)
    return {
        "market": entry,
        "installed": bool(current),
        "installed_version": current["version"] if current else "",
        "enabled": bool(current["enabled"]) if current else False,
        "health": current.get("health") if current else None,
    }


@router.get("/updates")
async def list_updates() -> dict[str, Any]:
    runtime = _runtime()
    items: list[dict[str, Any]] = []
    for item in await runtime.list_installed():
        if item["builtin"]:
            continue
        entry = await _catalog_entry(item["plugin_id"])
        if entry is None or not entry.get("installable", True):
            continue
        latest = str(entry.get("version") or "")
        if latest and latest != item["version"]:
            items.append(
                {
                    "plugin_id": item["plugin_id"],
                    "name": item["name"],
                    "current_version": item["version"],
                    "latest_version": latest,
                    "dist": entry.get("dist") or {},
                }
            )
    return {"items": items, "total": len(items)}


# ============================================================
# 安装与任务
# ============================================================


async def _run_install_job(job_id: str, user_id: str, coroutine: Any) -> None:
    try:
        item = await coroutine
        jobs.update_job(
            job_id,
            status="done",
            stage="done",
            message="安装完成",
            result=item,
        )
    except PluginError as error:
        jobs.fail_job(job_id, error.message)
    except Exception as error:  # noqa: BLE001 - 后台任务必须写回任务表
        logger.exception("插件安装任务失败: %s", job_id)
        jobs.fail_job(job_id, f"安装失败: {error}")


def _start_job(job: dict[str, Any], coroutine: Any) -> None:
    asyncio.create_task(
        _run_install_job(job["job_id"], str(job["_user_id"]), coroutine),
        name=f"plugin-install-{job['job_id']}",
    )


@router.post("/install", status_code=202)
async def install_plugin(body: InstallRequest) -> dict[str, Any]:
    user_id = current_user_id()
    runtime = get_plugin_runtime(user_id)
    await runtime.initialize()

    if body.source == "remote":
        if not body.manifest:
            raise HTTPException(status_code=422, detail="远程安装需要提供 manifest")
        plugin_id = str(body.manifest.get("plugin_id") or body.plugin_id)
        job = jobs.create_job("remote", plugin_id, user_id=user_id, plugin_id=plugin_id)
        _start_job(
            job,
            installer.register_remote(
                runtime,
                body.manifest,
                auto_enable=body.auto_enable,
            ),
        )
        return {"job_id": job["job_id"], "job": jobs.get_job(user_id, job["job_id"])}

    if body.source == "url" or (body.source == "market" and body.url):
        url = body.url
        sha256 = body.sha256
        plugin_id = body.plugin_id
        if not url:
            raise HTTPException(status_code=422, detail="缺少插件下载地址")
    else:
        entry = await _catalog_entry(body.plugin_id, body.version)
        if entry is None:
            raise HTTPException(status_code=404, detail="市场中没有该插件或版本")
        if entry.get("installable") is False:
            raise HTTPException(
                status_code=409,
                detail=entry.get("unsupported_reason")
                or "该市场条目不可直接安装",
            )
        dist = entry.get("dist") or {}
        url = str(dist.get("url") or "")
        sha256 = str(dist.get("sha256") or "")
        plugin_id = str(entry.get("plugin_id") or body.plugin_id)
        if not url:
            raise HTTPException(status_code=422, detail="市场条目缺少 dist.url")

    job = jobs.create_job("url", plugin_id or body.url, user_id=user_id, plugin_id=plugin_id)
    _start_job(
        job,
        installer.install_from_url(
            runtime,
            url,
            sha256=sha256,
            auto_enable=body.auto_enable,
            job_id=job["job_id"],
            sandbox_provider=lambda: _sandbox_backend(user_id),
        ),
    )
    return {"job_id": job["job_id"], "job": jobs.get_job(user_id, job["job_id"])}


@router.post("/install/upload", status_code=202)
async def upload_plugin(
    file: UploadFile = File(...),
    auto_enable: bool = Form(True),
) -> dict[str, Any]:
    config = _config()
    data = await file.read(config.PLUGIN_MAX_PACKAGE_BYTES + 1)
    if len(data) > config.PLUGIN_MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail="插件包超过体积限制")
    filename = file.filename or "plugin.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="只支持 ZIP 插件包")
    user_id = current_user_id()
    runtime = get_plugin_runtime(user_id)
    await runtime.initialize()
    job = jobs.create_job("zip", filename, user_id=user_id)
    _start_job(
        job,
        installer.install_archive(
            runtime,
            data,
            filename=filename,
            auto_enable=auto_enable,
            job_id=job["job_id"],
            sandbox_provider=lambda: _sandbox_backend(user_id),
        ),
    )
    return {"job_id": job["job_id"], "job": jobs.get_job(user_id, job["job_id"])}


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": jobs.list_jobs(current_user_id())}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get_job(current_user_id(), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="安装任务不存在或已过期")
    return job


@router.get("/audit")
async def list_plugin_audit(
    plugin_id: str = Query("", max_length=64),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """当前用户的插件审计：安装、启停、重启、卸载、验证与配置变更。"""
    runtime = _runtime()
    await runtime.initialize()
    items = await runtime.store.list_audit(
        current_user_id(), plugin_id=plugin_id, limit=limit
    )
    return {"items": items, "total": len(items)}


# ============================================================
# 已安装插件
# ============================================================


@router.get("")
async def list_plugins(
    keyword: str = Query("", max_length=200),
    enabled: bool | None = Query(None),
    source: str = Query("", max_length=32),
) -> dict[str, Any]:
    runtime = _runtime()
    items = await runtime.list_installed()
    needle = keyword.strip().casefold()
    if needle:
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [item["plugin_id"], item["name"], item["description"]]
            ).casefold()
        ]
    if enabled is not None:
        items = [item for item in items if item["enabled"] is enabled]
    if source:
        items = [item for item in items if item["source"] == source]
    return {"items": items, "total": len(items)}


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        return await _runtime().detail(plugin_id)
    except PluginError as error:
        raise _http(error)


@router.get("/{plugin_id}/capabilities")
async def get_capabilities(plugin_id: str) -> dict[str, Any]:
    try:
        return await _runtime().capabilities(plugin_id)
    except PluginError as error:
        raise _http(error)


@router.get("/{plugin_id}/health")
async def get_health(plugin_id: str) -> dict[str, Any]:
    try:
        return await _runtime().health(plugin_id)
    except PluginError as error:
        raise _http(error)


@router.get("/{plugin_id}/config")
async def get_config(plugin_id: str) -> dict[str, Any]:
    try:
        config = await _runtime().get_config(plugin_id)
    except PluginError as error:
        raise _http(error)
    return {"plugin_id": plugin_id, "config": config}


@router.put("/{plugin_id}/config")
async def save_config(plugin_id: str, body: ConfigRequest) -> dict[str, Any]:
    try:
        config = await _runtime().save_config(
            plugin_id, body.config, secret_ref=body.secret_ref
        )
    except PluginError as error:
        raise _http(error)
    return {"plugin_id": plugin_id, "config": config}


@router.post("/{plugin_id}/toggle")
async def toggle_plugin(plugin_id: str, body: ToggleRequest) -> dict[str, Any]:
    try:
        result = await _runtime().toggle(plugin_id, body.enabled)
    except PluginError as error:
        raise _http(error)
    return {"plugin_id": plugin_id, "enabled": body.enabled, "plugin": result}


@router.post("/{plugin_id}/restart")
async def restart_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        return await _runtime().restart(plugin_id)
    except PluginError as error:
        raise _http(error)


@router.post("/{plugin_id}/update", status_code=202)
async def update_plugin(plugin_id: str, body: UpdateRequest) -> dict[str, Any]:
    runtime = _runtime()
    try:
        installed = _find_installed(runtime, plugin_id)
    except PluginError as error:
        raise _http(error)
    entry = await _catalog_entry(plugin_id, body.version)
    if entry is None:
        raise HTTPException(status_code=404, detail="市场中没有目标版本")
    if entry.get("installable") is False:
        raise HTTPException(
            status_code=409,
            detail=entry.get("unsupported_reason") or "该市场条目不可直接安装",
        )
    target_version = str(entry.get("version") or "")
    if target_version == installed.version:
        raise HTTPException(status_code=409, detail="目标版本与当前版本相同")
    dist = entry.get("dist") or {}
    url = str(dist.get("url") or "")
    if not url:
        raise HTTPException(status_code=422, detail="市场条目缺少 dist.url")
    user_id = current_user_id()
    job = jobs.create_job(
        "update", f"{plugin_id} -> {target_version}", user_id=user_id, plugin_id=plugin_id
    )
    _start_job(
        job,
        installer.install_from_url(
            runtime,
            url,
            sha256=str(dist.get("sha256") or ""),
            auto_enable=True,
            job_id=job["job_id"],
            sandbox_provider=lambda: _sandbox_backend(user_id),
        ),
    )
    return {"job_id": job["job_id"], "job": jobs.get_job(user_id, job["job_id"])}


@router.delete("/{plugin_id}")
async def delete_plugin(plugin_id: str) -> dict[str, bool]:
    try:
        await _runtime().uninstall(plugin_id)
    except PluginError as error:
        raise _http(error)
    return {"ok": True}


# ============================================================
# UI 静态资源
# ============================================================


@router.get("/{plugin_id}/ui/{path:path}")
async def plugin_ui(plugin_id: str, path: str) -> FileResponse:
    runtime = _runtime()
    try:
        installed = _find_installed(runtime, plugin_id)
    except PluginError as error:
        raise _http(error)
    if not runtime.enabled_plugin_ids() or plugin_id not in runtime.enabled_plugin_ids():
        raise HTTPException(status_code=403, detail="插件未启用")
    candidate = storage.resolve_ui_file(installed.path, path)
    if candidate is None:
        raise HTTPException(status_code=404, detail="UI 资源不存在")
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                f"connect-src 'none'; frame-ancestors {_frame_ancestors()}"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
