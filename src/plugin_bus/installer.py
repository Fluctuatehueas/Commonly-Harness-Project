"""插件安装编排：下载/解压 → 静态扫描 → 沙箱试运行 → 落盘 → 登记 → 启动。

接口层只负责参数校验与 job 返回，真正的安装步骤集中在这里，保证市场、
上传、URL 与命令系统走同一条安全路径。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from plugin_bus import jobs, scanner, storage
from plugin_bus.errors import PluginConflict, PluginError, PluginPackageInvalid
from plugin_bus.manifest import Manifest, load_manifest, manifest_from_dict
from plugin_bus.sandbox_verify import verify_package
from plugin_bus.runtime import PluginRuntime

logger = logging.getLogger(__name__)


def _config() -> Any:
    from agent import config

    return config


async def _download(url: str, *, max_bytes: int) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise PluginPackageInvalid("插件链接必须以 http(s) 开头")
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise PluginPackageInvalid(
                    f"下载插件失败：HTTP {response.status_code}"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PluginPackageInvalid("插件包超过体积限制")
                chunks.append(chunk)
    return b"".join(chunks)


def _invalidate(runtime: PluginRuntime) -> None:
    try:
        from agent import file_index

        file_index.invalidate("plugins", runtime.user_id)
    except Exception:
        logger.debug("插件索引失效失败", exc_info=True)


async def _audit(
    runtime: PluginRuntime,
    plugin_id: str,
    action: str,
    *,
    result: str = "ok",
    version: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        await runtime.store.audit(
            user_id=runtime.user_id,
            plugin_id=plugin_id,
            version=version,
            action=action,
            result=result,
            detail=detail or {},
        )
    except Exception:
        logger.debug("插件审计写入失败", exc_info=True)


async def install_archive(
    runtime: PluginRuntime,
    data: bytes,
    *,
    filename: str = "plugin.zip",
    source: str = "user",
    auto_enable: bool = True,
    job_id: str = "",
    sandbox_backend: Any | None = None,
    sandbox_provider: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """安装 ZIP 包：校验、扫描、试运行、落盘、登记，可选立即启动。"""
    config = _config()
    if job_id:
        jobs.update_job(job_id, status="running", stage="extracting", message="正在解压插件包")
    if len(data) > config.PLUGIN_MAX_PACKAGE_BYTES:
        raise PluginPackageInvalid(
            f"插件包超过 {config.PLUGIN_MAX_PACKAGE_BYTES // (1024 * 1024)} MB 限制"
        )
    storage.check_quota(
        runtime.user_id,
        max_bytes=config.PLUGIN_USER_QUOTA_BYTES,
        max_plugins=config.PLUGIN_USER_MAX_PLUGINS,
        adding=True,
    )
    digest = hashlib.sha256(data).hexdigest()

    temporary_root = Path(config.PLUGIN_ROOT) / "users" / runtime.user_id / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root) as temporary_name:
        temporary = Path(temporary_name)
        package_root = storage.safe_extract_zip(
            data, temporary / "extracted", max_bytes=config.PLUGIN_MAX_PACKAGE_BYTES
        )
        manifest = load_manifest(package_root / "manifest.json", source=source)
        await _install_extracted(
            runtime,
            manifest=manifest,
            package_root=package_root,
            digest=digest,
            source=source,
            auto_enable=auto_enable,
            job_id=job_id,
            sandbox_backend=sandbox_backend,
            sandbox_provider=sandbox_provider,
        )
    item = await runtime.detail(manifest.plugin_id)
    return item


async def _install_extracted(
    runtime: PluginRuntime,
    *,
    manifest: Manifest,
    package_root: Path,
    digest: str,
    source: str,
    auto_enable: bool,
    job_id: str,
    sandbox_backend: Any | None,
    sandbox_provider: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    config = _config()
    plugin_id = manifest.plugin_id
    if job_id:
        jobs.update_job(job_id, stage="scanning", message="正在静态扫描")
    report = scanner.scan_directory(
        package_root,
        max_bytes=config.PLUGIN_MAX_PACKAGE_BYTES,
    )
    if not report.ok:
        await _audit(
            runtime,
            plugin_id,
            "verify",
            result="failed",
            version=manifest.version,
            detail=report.to_dict(),
        )
        raise PluginPackageInvalid(
            "静态扫描未通过: "
            + "; ".join(issue.message for issue in report.issues if issue.level == "high")
        )

    if config.PLUGIN_SANDBOX_VERIFY:
        if job_id:
            jobs.update_job(job_id, stage="sandbox_verifying", message="正在沙箱试运行")
        backend = sandbox_backend
        if backend is None and sandbox_provider is not None:
            backend = await sandbox_provider()
        await verify_package(
            package_root=package_root,
            manifest=manifest,
            sandbox_backend=backend,
            required=True,
        )

    installed = runtime.installed_map().get(plugin_id)
    if installed is not None and installed.source == "builtin":
        raise PluginConflict(
            f"不允许安装与系统内置同 plugin_id 的插件: {plugin_id}",
            plugin_id=plugin_id,
        )
    target = storage.version_dir(runtime.user_id, plugin_id, manifest.version)
    if target.exists():
        raise PluginConflict(
            f"插件版本已存在: {plugin_id}@{manifest.version}", plugin_id=plugin_id
        )
    if job_id:
        jobs.update_job(job_id, stage="installing", message="正在安装插件")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(package_root, target)
        storage.ensure_plugin_dirs(runtime.user_id, plugin_id, manifest.version)
        storage.write_current(runtime.user_id, plugin_id, manifest.version)
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        raise PluginError(
            f"安装插件失败: {error}", code="PLUGIN_INSTALL_FAILED", plugin_id=plugin_id
        ) from error

    await runtime.store.upsert_install(
        user_id=runtime.user_id,
        plugin_id=plugin_id,
        version=manifest.version,
        source=source,
        runtime_kind=manifest.runtime.kind,
        transport_kind=manifest.transport.kind,
        endpoint=manifest.transport.url,
        sha256=digest,
        status="verified",
    )
    await runtime.store.upsert_user_state(
        user_id=runtime.user_id,
        plugin_id=plugin_id,
        enabled=bool(auto_enable),
        current_version=manifest.version,
        auto_disabled=False,
    )
    await _audit(
        runtime,
        plugin_id,
        "install",
        version=manifest.version,
        detail={"source": source, "sha256": digest, "scan": report.to_dict()},
    )
    _invalidate(runtime)
    await runtime.refresh_installed()
    # 版本切换后必须停掉旧进程，toggle 才会用新版本重新握手。
    try:
        await runtime.manager.stop(plugin_id, status="enabled")
    except Exception:
        logger.debug("安装后停止旧插件进程失败: %s", plugin_id, exc_info=True)

    if auto_enable:
        if job_id:
            jobs.update_job(job_id, stage="starting", message="正在启动插件")
        try:
            await runtime.toggle(plugin_id, True)
        except Exception as error:
            # 包已经过验证并安装成功，启动失败不回收包，交由用户重试/排障。
            logger.warning("插件安装完成但启动失败: %s (%s)", plugin_id, error)
            await _audit(
                runtime,
                plugin_id,
                "failure",
                result="failed",
                version=manifest.version,
                detail={"error": str(error), "phase": "start"},
            )


async def install_from_url(
    runtime: PluginRuntime,
    url: str,
    *,
    sha256: str = "",
    source: str = "community",
    auto_enable: bool = True,
    job_id: str = "",
    sandbox_backend: Any | None = None,
    sandbox_provider: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    config = _config()
    if job_id:
        jobs.update_job(job_id, status="running", stage="downloading", message="正在下载插件")
    data = await _download(url, max_bytes=config.PLUGIN_MAX_PACKAGE_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and digest.lower() != sha256.lower():
        raise PluginPackageInvalid("插件包 sha256 校验失败")
    return await install_archive(
        runtime,
        data,
        filename=url.rsplit("/", 1)[-1] or "plugin.zip",
        source=source,
        auto_enable=auto_enable,
        job_id=job_id,
        sandbox_backend=sandbox_backend,
        sandbox_provider=sandbox_provider,
    )


async def register_remote(
    runtime: PluginRuntime,
    manifest_raw: dict[str, Any],
    *,
    auto_enable: bool = True,
    source: str = "remote",
) -> dict[str, Any]:
    """注册远程插件：不落包，只登记 endpoint 与协议版本。"""
    manifest = manifest_from_dict(manifest_raw, source=source)
    if manifest.runtime.kind != "remote":
        raise PluginPackageInvalid("远程注册要求 runtime.kind=remote")
    if manifest.plugin_id in runtime.installed_map():
        raise PluginConflict(
            f"插件已存在: {manifest.plugin_id}", plugin_id=manifest.plugin_id
        )
    await runtime.store.upsert_install(
        user_id=runtime.user_id,
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        source=source,
        runtime_kind="remote",
        transport_kind="http",
        endpoint=manifest.transport.url,
        sha256="",
        status="verified",
    )
    await runtime.store.upsert_user_state(
        user_id=runtime.user_id,
        plugin_id=manifest.plugin_id,
        enabled=bool(auto_enable),
        current_version=manifest.version,
        auto_disabled=False,
    )
    # 远程插件没有包目录，manifest 落 plugin_config 的保留键，供重启后恢复。
    await runtime.store.save_config(
        user_id=runtime.user_id,
        plugin_id=manifest.plugin_id,
        config={"__manifest__": manifest_raw},
    )
    await _audit(
        runtime,
        manifest.plugin_id,
        "install",
        version=manifest.version,
        detail={"source": source, "endpoint": manifest.transport.url},
    )
    await runtime.refresh_installed()
    # 远程插件没有版本目录，运行时用内存 manifest 参与启动。
    runtime.register_remote_manifest(manifest)
    if auto_enable:
        try:
            await runtime.toggle(manifest.plugin_id, True)
        except Exception as error:
            logger.warning("远程插件注册后连接失败: %s (%s)", manifest.plugin_id, error)
    return await runtime.detail(manifest.plugin_id)


__all__ = ["install_archive", "install_from_url", "register_remote"]
