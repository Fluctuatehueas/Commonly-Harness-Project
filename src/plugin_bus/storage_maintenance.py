"""插件存储维护：旧版本清理、日志轮转、临时目录回收。

由 ``agent.storage_maintenance`` 周期调用；任何异常只记录，不影响服务。
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin_bus.manifest import parse_semver
from plugin_bus.storage import read_current

logger = logging.getLogger(__name__)

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP = 3
TMP_TTL_SECONDS = 24 * 3600


def _config() -> Any:
    from agent import config

    return config


def rotate_log(path: Path, *, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    for index in range(keep, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        try:
            if source.is_file():
                target.unlink(missing_ok=True)
                source.replace(target)
        except OSError:
            logger.debug("插件日志轮转失败：%s -> %s", source, target, exc_info=True)
            return False
    return True


def _user_dirs() -> list[Path]:
    root = Path(_config().PLUGIN_USERS_ROOT)
    if not root.is_dir():
        return []
    return [item for item in root.iterdir() if item.is_dir()]


def cleanup_old_versions(keep: int | None = None) -> int:
    """每个插件保留最近 N 个版本，当前指针版本永不删除。"""
    keep_count = max(1, int(keep or _config().PLUGIN_VERSION_KEEP))
    removed = 0
    for user_dir in _user_dirs():
        user_id = user_dir.name
        for plugin_dir in user_dir.iterdir():
            versions_root = plugin_dir / "versions"
            if not plugin_dir.is_dir() or not versions_root.is_dir():
                continue
            versions = [
                item
                for item in versions_root.iterdir()
                if item.is_dir() and parse_semver(item.name) is not None
            ]
            if len(versions) <= keep_count:
                continue
            versions.sort(key=lambda item: parse_semver(item.name) or (0, 0, 0))
            current = read_current(user_id, plugin_dir.name)
            protected = {current} if current else set()
            protected.update(item.name for item in versions[-keep_count:])
            for item in versions:
                if item.name in protected:
                    continue
                try:
                    shutil.rmtree(item)
                    removed += 1
                    logger.info(
                        "清理旧插件版本：%s/%s/%s", user_id, plugin_dir.name, item.name
                    )
                except OSError:
                    logger.debug("清理旧插件版本失败：%s", item, exc_info=True)
    return removed


def cleanup_logs() -> int:
    rotated = 0
    for user_dir in _user_dirs():
        for plugin_dir in user_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            logs = plugin_dir / "logs"
            if not logs.is_dir():
                continue
            for name in ("stdio.log", "plugin.log"):
                if rotate_log(logs / name):
                    rotated += 1
    return rotated


def cleanup_tmp() -> int:
    """清理安装临时目录；超过 24 小时未更新才删除。"""
    now = time.time()
    removed = 0
    for user_dir in _user_dirs():
        tmp_root = user_dir / ".tmp"
        if not tmp_root.is_dir():
            continue
        for item in tmp_root.iterdir():
            try:
                if now - item.stat().st_mtime <= TMP_TTL_SECONDS:
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink(missing_ok=True)
                removed += 1
            except OSError:
                logger.debug("清理插件临时目录失败：%s", item, exc_info=True)
    return removed


def run_once() -> dict[str, int]:
    """执行一轮插件维护；返回各项处理数量，便于测试与日志。"""
    result = {
        "versions_removed": cleanup_old_versions(),
        "logs_rotated": cleanup_logs(),
        "tmp_removed": cleanup_tmp(),
    }
    if any(result.values()):
        logger.info(
            "插件存储维护完成：%s（%s）", result, datetime.now(timezone.utc).isoformat()
        )
    return result


__all__ = [
    "rotate_log",
    "cleanup_old_versions",
    "cleanup_logs",
    "cleanup_tmp",
    "run_once",
]
