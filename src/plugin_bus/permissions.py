"""总线侧权限拦截与 ``host.*`` 白名单。

权限只认 manifest 显式声明；未声明即拒绝，不做隐式放行。插件进程内没有宿主
对象，只能通过 ``host.*`` 访问被允许的有限能力。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from plugin_bus import protocol
from plugin_bus.errors import PluginPermissionDenied
from plugin_bus.manifest import Manifest

logger = logging.getLogger(__name__)

HOST_PERMISSION_MAP: dict[str, str | None] = {
    protocol.HOST_STORAGE_GET: "storage",
    protocol.HOST_STORAGE_SET: "storage",
    protocol.HOST_STORAGE_LIST: "storage",
    protocol.HOST_STORAGE_DELETE: "storage",
    protocol.HOST_SECRET_GET: "secret",
    protocol.HOST_LOG: None,
    protocol.HOST_PROGRESS: None,
}

MAX_LOG_BYTES = 5 * 1024 * 1024


def require_permission(manifest: Manifest, permission: str) -> None:
    if not manifest.has_permission(permission):
        raise PluginPermissionDenied(
            f"插件 {manifest.plugin_id} 未声明权限: {permission}",
            plugin_id=manifest.plugin_id,
            detail={"permission": permission},
        )


def host_permission(method: str) -> str | None:
    if method not in HOST_PERMISSION_MAP:
        raise PluginPermissionDenied(
            f"未开放的宿主方法: {method}",
            detail={"method": method},
        )
    return HOST_PERMISSION_MAP[method]


def ensure_host_allowed(manifest: Manifest, method: str) -> None:
    permission = host_permission(method)
    if permission is None:
        return
    require_permission(manifest, permission)


def resolve_data_path(data_root: Path, relative: str) -> Path:
    """解析插件私有数据路径，拒绝绝对路径与路径穿越。"""
    raw = str(relative or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or any(part == ".." for part in raw.split("/")):
        raise PluginPermissionDenied(f"非法的数据路径: {relative!r}")
    root = data_root.resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PluginPermissionDenied(f"数据路径越界: {relative!r}") from error
    return candidate


class HostBridge:
    """单个插件连接的反向调用处理器。"""

    def __init__(
        self,
        *,
        manifest: Manifest,
        user_id: str,
        data_root: Path,
        logs_root: Path,
        progress: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.manifest = manifest
        self.user_id = user_id
        self.data_root = data_root
        self.logs_root = logs_root
        self._progress = progress
        self._lock = asyncio.Lock()

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        ensure_host_allowed(self.manifest, method)
        if method == protocol.HOST_STORAGE_GET:
            return await self._storage_get(params)
        if method == protocol.HOST_STORAGE_SET:
            return await self._storage_set(params)
        if method == protocol.HOST_STORAGE_LIST:
            return await self._storage_list()
        if method == protocol.HOST_STORAGE_DELETE:
            return await self._storage_delete(params)
        if method == protocol.HOST_SECRET_GET:
            return self._secret_get(params)
        if method == protocol.HOST_LOG:
            self._write_log(params)
            return {}
        if method == protocol.HOST_PROGRESS:
            return await self._emit_progress(params)
        raise PluginPermissionDenied(f"未开放的宿主方法: {method}")

    # ---------- storage：单文件 JSON KV，避免插件直接接触宿主文件系统 ----------

    def _kv_path(self) -> Path:
        self.data_root.mkdir(parents=True, exist_ok=True)
        return self.data_root / "kv.json"

    def _read_kv(self) -> dict[str, Any]:
        path = self._kv_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_kv(self, payload: dict[str, Any]) -> None:
        path = self._kv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    async def _storage_get(self, params: dict[str, Any]) -> Any:
        key = str(params.get("key") or "").strip()
        if not key:
            raise PluginPermissionDenied("host.storage.get 缺少 key")
        async with self._lock:
            return self._read_kv().get(key)

    async def _storage_set(self, params: dict[str, Any]) -> dict[str, Any]:
        key = str(params.get("key") or "").strip()
        if not key:
            raise PluginPermissionDenied("host.storage.set 缺少 key")
        value = params.get("value")
        async with self._lock:
            payload = self._read_kv()
            payload[key] = value
            self._write_kv(payload)
        return {"ok": True}

    async def _storage_list(self) -> list[str]:
        async with self._lock:
            return sorted(self._read_kv().keys())

    async def _storage_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        key = str(params.get("key") or "").strip()
        async with self._lock:
            payload = self._read_kv()
            payload.pop(key, None)
            self._write_kv(payload)
        return {"ok": True}

    # ---------- secret / log / progress ----------

    def _secret_get(self, params: dict[str, Any]) -> str:
        name = str(params.get("name") or "").strip()
        if not name:
            raise PluginPermissionDenied("host.secret.get 缺少 name")
        env_key = f"HARNESS_PLUGIN_SECRET_{self.manifest.plugin_id}_{name}".upper()
        env_key = "".join(char if char.isalnum() or char == "_" else "_" for char in env_key)
        value = os.getenv(env_key)
        if not value:
            raise PluginPermissionDenied(
                f"插件 {self.manifest.plugin_id} 未配置密钥: {name}",
                plugin_id=self.manifest.plugin_id,
            )
        return value

    def _write_log(self, params: dict[str, Any]) -> None:
        level = str(params.get("level") or "info").lower()
        message = str(params.get("message") or "")
        self.logs_root.mkdir(parents=True, exist_ok=True)
        path = self.logs_root / "plugin.log"
        try:
            if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                path.replace(path.with_suffix(".log.1"))
        except OSError:
            pass
        try:
            with path.open("a", encoding="utf-8") as handle:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                handle.write(f"{stamp} [{level}] {message[:4000]}\n")
        except OSError:
            logger.debug("写入插件日志失败", exc_info=True)

    async def _emit_progress(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._progress is not None:
            result = self._progress(params)
            if inspect.isawaitable(result):
                await result
        return {"ok": True}


__all__ = [
    "HostBridge",
    "HOST_PERMISSION_MAP",
    "require_permission",
    "host_permission",
    "ensure_host_allowed",
    "resolve_data_path",
]
