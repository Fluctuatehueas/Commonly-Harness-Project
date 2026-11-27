"""v1.1 存储维护：MCP 日志轮转、npm 缓存配额、闲置运行态清理。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_LOG_MAX_BYTES = 5 * 1024 * 1024
MCP_LOG_KEEP = 2
MCP_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
MCP_RUNTIME_IDLE_SECONDS = 7 * 24 * 3600
INTERVAL_SECONDS = 6 * 3600


def _config() -> Any:
    from agent import config

    return config


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def rotate_log(path: Path) -> bool:
    """单个日志超过阈值时轮转；保留 .1 / .2。"""
    try:
        if not path.is_file() or path.stat().st_size <= MCP_LOG_MAX_BYTES:
            return False
    except OSError:
        return False
    for index in range(MCP_LOG_KEEP, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        try:
            if source.is_file():
                target.unlink(missing_ok=True)
                source.replace(target)
        except OSError:
            logger.debug("MCP 日志轮转失败：%s -> %s", source, target, exc_info=True)
            return False
    return True


def _runtime_dirs() -> list[Path]:
    root = Path(_config().MCP_ROOT) / "users"
    if not root.is_dir():
        return []
    return [
        path
        for path in root.glob("*/runtime/*")
        if path.is_dir()
    ]


def cleanup_runtime() -> None:
    now = time.time()
    for runtime in _runtime_dirs():
        try:
            if now - runtime.stat().st_mtime <= MCP_RUNTIME_IDLE_SECONDS:
                continue
            shutil.rmtree(runtime)
            logger.info("清理闲置 MCP 运行态：%s", runtime)
        except OSError:
            logger.debug("清理 MCP 运行态失败：%s", runtime, exc_info=True)


def cleanup_npm_cache() -> None:
    root = Path(_config().MCP_ROOT) / "users"
    if not root.is_dir():
        return
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        caches: list[tuple[float, int, Path]] = []
        for cache in user_dir.glob("runtime/*/npm-cache"):
            if not cache.is_dir():
                continue
            try:
                mtime = cache.stat().st_mtime
            except OSError:
                continue
            caches.append((mtime, _dir_size(cache), cache))
        total = sum(item[1] for item in caches)
        if total <= MCP_CACHE_MAX_BYTES:
            continue
        for _mtime, size, cache in sorted(caches):
            if total <= MCP_CACHE_MAX_BYTES:
                break
            try:
                shutil.rmtree(cache)
                total -= size
                logger.info("清理 MCP npm 缓存：%s", cache)
            except OSError:
                logger.debug("清理 MCP npm 缓存失败：%s", cache, exc_info=True)


def run_once() -> None:
    """执行一轮维护；任何异常只记录，不影响服务。"""
    for runtime in _runtime_dirs():
        rotate_log(runtime / "logs" / "stdio.log")
    cleanup_runtime()
    cleanup_npm_cache()
    # 插件旧版本、日志与安装临时目录；插件模块异常不阻断 MCP 维护。
    try:
        from plugin_bus.storage_maintenance import run_once as run_plugin_maintenance

        run_plugin_maintenance()
    except Exception:  # noqa: BLE001 - 维护任务不能拖垮主进程
        logger.warning("插件存储维护失败", exc_info=True)


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:  # noqa: BLE001 - 维护任务不能拖垮主进程
            logger.warning("存储维护任务失败", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)


async def start_storage_maintenance() -> asyncio.Task[None] | None:
    try:
        await asyncio.to_thread(run_once)
    except Exception:  # noqa: BLE001
        logger.warning("启动存储维护失败", exc_info=True)
    return asyncio.create_task(_loop(), name="storage-maintenance")


async def stop_storage_maintenance(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
