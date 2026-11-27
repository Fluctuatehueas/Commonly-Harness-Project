"""插件市场目录：公共源、私有源、分片校验、缓存与 stale 回退。

目录只读、不带凭据；写操作仍由安装接口与 Plugin Bus 完成。任何网络失败都回退
到上一次成功缓存并标记 ``stale``，不把空目录当好数据覆盖。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from plugin_bus import catalog_dsh

logger = logging.getLogger(__name__)

SOURCE_PRIORITY = {"private": 0, "local": 1, "public": 2, "dsh": 3}
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


def _config() -> Any:
    from agent import config

    return config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CatalogResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    stale: bool = False
    fetched_at: str = ""
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SourceSpec:
    name: str
    url: str
    kind: str  # http / local / dsh


class CatalogService:
    """进程内目录服务；按用户无关，缓存 10 分钟。"""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._lock = asyncio.Lock()
        self._result: CatalogResult | None = None
        self._all_items: list[dict[str, Any]] = []
        self._cache_at = 0.0
        self._source_cache: dict[str, dict[str, Any]] = {}
        self._disk_loaded = False

    # ---------- 对外 ----------

    async def get(self, *, force: bool = False) -> CatalogResult:
        self._ensure_disk_loaded()
        config = _config()
        ttl = config.PLUGIN_CATALOG_CACHE_SECONDS
        now = time.monotonic()
        if not force and self._result is not None and now - self._cache_at < ttl:
            return self._result
        async with self._lock:
            if not force and self._result is not None and now - self._cache_at < ttl:
                return self._result
            result = await self._refresh()
            self._result = result
            self._cache_at = time.monotonic()
            return result

    async def find(
        self, plugin_id: str, version: str = "", *, force: bool = False
    ) -> dict[str, Any] | None:
        await self.get(force=force)
        matches = [
            item for item in self._all_items if str(item.get("plugin_id")) == plugin_id
        ]
        if not matches:
            return None
        if version:
            for item in matches:
                if str(item.get("version")) == version:
                    return item
            return None
        matches.sort(key=lambda item: _version_key(str(item.get("version") or "")))
        return matches[-1]

    # ---------- 源与拉取 ----------

    def _source_specs(self) -> list[SourceSpec]:
        config = _config()
        specs: list[SourceSpec] = []
        if config.PLUGIN_MARKET_ENABLED:
            if config.PLUGIN_CATALOG_URL:
                specs.append(SourceSpec("public", config.PLUGIN_CATALOG_URL, "http"))
            elif Path(config.PLUGIN_CATALOG_PATH).is_file():
                specs.append(
                    SourceSpec("local", str(config.PLUGIN_CATALOG_PATH), "local")
                )
        if config.PLUGIN_REGISTRY_URL:
            specs.append(SourceSpec("private", config.PLUGIN_REGISTRY_URL, "http"))
        if config.PLUGIN_DSH_ENABLED and config.PLUGIN_DSH_CATALOG_URL:
            specs.append(SourceSpec("dsh", config.PLUGIN_DSH_CATALOG_URL, "dsh"))
        return specs

    async def _refresh(self) -> CatalogResult:
        specs = self._source_specs()
        fetched: list[tuple[SourceSpec, list[dict[str, Any]], bool, str, str]] = []
        if specs:
            results = await asyncio.gather(
                *(self._fetch_source(spec) for spec in specs),
                return_exceptions=True,
            )
            for spec, result in zip(specs, results):
                if isinstance(result, Exception):
                    cached = self._source_cache.get(spec.name)
                    fetched.append(
                        (spec, list(cached["items"]) if cached else [], True, str(result), "")
                    )
                else:
                    entries, stale, error, fetched_at = result
                    fetched.append((spec, entries, stale, error, fetched_at))

        all_items: list[dict[str, Any]] = []
        sources: dict[str, dict[str, Any]] = {}
        stale_any = False
        for spec, entries, stale, error, fetched_at in fetched:
            sources[spec.name] = {
                "url": spec.url,
                "ok": not error,
                "stale": bool(stale),
                "count": len(entries),
                "error": error,
                "fetched_at": fetched_at,
            }
            stale_any = stale_any or bool(stale)
            if entries:
                self._source_cache[spec.name] = {
                    "items": entries,
                    "fetched_at": fetched_at or _now_iso(),
                }
                all_items.extend(entries)

        whitelist = {str(item) for item in _config().PLUGIN_REGISTRY_WHITELIST}
        if whitelist:
            all_items = [
                item
                for item in all_items
                if str(item.get("plugin_id")) in whitelist
            ]

        self._all_items = all_items
        result = CatalogResult(
            items=self._latest_items(all_items),
            stale=stale_any,
            fetched_at=_now_iso(),
            sources=sources,
        )
        self._save_disk()
        return result

    def _latest_items(self, all_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """每个 plugin_id 只展示最高优先级源的最新版本，并附版本历史。"""
        ordered = sorted(
            all_items,
            key=lambda item: (
                SOURCE_PRIORITY.get(str(item.get("source") or ""), 9),
                _version_key(str(item.get("version") or "")),
            ),
        )
        best: dict[str, dict[str, Any]] = {}
        versions: dict[str, set[str]] = {}
        for item in ordered:
            plugin_id = str(item.get("plugin_id") or "")
            if not plugin_id:
                continue
            versions.setdefault(plugin_id, set()).add(str(item.get("version") or ""))
            # 高优先级源先进入；同源只保留最高版本。
            current = best.get(plugin_id)
            if current is None:
                best[plugin_id] = dict(item)
            elif current.get("source") == item.get("source"):
                if _version_key(str(item.get("version") or "")) > _version_key(
                    str(current.get("version") or "")
                ):
                    best[plugin_id] = dict(item)
        for plugin_id, item in best.items():
            item["versions"] = sorted(
                versions.get(plugin_id, set()), key=_version_key
            )
        return list(best.values())

    async def _fetch_source(
        self, spec: SourceSpec
    ) -> tuple[list[dict[str, Any]], bool, str, str]:
        """返回 (条目, stale, error, fetched_at)。不抛异常。"""
        try:
            if spec.kind == "local":
                payload = json.loads(Path(spec.url).read_text(encoding="utf-8-sig"))
                entries = self._extract(payload, spec.name)
                return entries, False, "", _now_iso()
            entries: list[dict[str, Any]] = []
            payload = await self._get_json(spec.url)
            entries.extend(self._extract(payload, spec.name))
            shards = payload.get("shards") if isinstance(payload, dict) else None
            if isinstance(shards, list):
                for shard in shards:
                    if not isinstance(shard, dict):
                        continue
                    url = str(shard.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        continue
                    data = await self._get_bytes(url)
                    expected = str(shard.get("sha256") or "").strip().lower()
                    if expected:
                        digest = hashlib.sha256(data).hexdigest()
                        if digest != expected:
                            raise ValueError(f"分片 sha256 校验失败: {url}")
                    entries.extend(self._extract(json.loads(data), spec.name))
            return entries, False, "", _now_iso()
        except Exception as error:  # noqa: BLE001 - 单源失败不影响其它源
            logger.warning("插件目录源拉取失败（%s）：%s", spec.name, error)
            cached = self._source_cache.get(spec.name)
            if cached:
                return (
                    list(cached.get("items") or []),
                    True,
                    f"{type(error).__name__}: {error}",
                    str(cached.get("fetched_at") or ""),
                )
            return [], True, f"{type(error).__name__}: {error}", ""

    def _extract(self, payload: Any, source: str) -> list[dict[str, Any]]:
        if source == "dsh":
            return catalog_dsh.map_entries(payload)
        raw_entries: list[Any]
        if isinstance(payload, list):
            raw_entries = payload
        elif isinstance(payload, dict) and isinstance(payload.get("plugins"), list):
            raw_entries = payload["plugins"]
        else:
            raw_entries = []
        entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            normalized = self._normalize(raw, source)
            if normalized is not None:
                entries.append(normalized)
        return entries

    @staticmethod
    def _normalize(raw: Any, source: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        plugin_id = str(raw.get("plugin_id") or raw.get("id") or "").strip()
        if not plugin_id:
            return None
        item = dict(raw)
        item["plugin_id"] = plugin_id
        item["name"] = str(item.get("name") or plugin_id)
        item["version"] = str(item.get("version") or "0.0.0")
        item["source"] = str(item.get("source") or source)
        dist = item.get("dist")
        item["dist"] = dist if isinstance(dist, dict) else {}
        if "installable" not in item:
            item["installable"] = bool(item["dist"].get("url"))
        return item

    # ---------- HTTP ----------

    async def _get_bytes(self, url: str) -> bytes:
        config = _config()
        async with httpx.AsyncClient(
            timeout=float(config.PLUGIN_HTTP_TIMEOUT_SECONDS),
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > MAX_DOCUMENT_BYTES:
                raise ValueError(f"目录文档超过体积上限: {url}")
            return response.content

    async def _get_json(self, url: str) -> Any:
        data = await self._get_bytes(url)
        return json.loads(data)

    # ---------- 磁盘缓存 ----------

    def _cache_path(self) -> Path:
        from agent.config import HARNESS_HOME

        return Path(HARNESS_HOME) / "cache" / "catalog" / "plugins.json"

    def _ensure_disk_loaded(self) -> None:
        if self._disk_loaded:
            return
        self._disk_loaded = True
        try:
            payload = json.loads(
                self._cache_path().read_text(encoding="utf-8-sig") or "{}"
            )
        except (OSError, json.JSONDecodeError):
            return
        sources = payload.get("sources") if isinstance(payload, dict) else None
        if not isinstance(sources, dict):
            return
        for name, item in sources.items():
            if isinstance(item, dict) and isinstance(item.get("items"), list):
                self._source_cache[str(name)] = {
                    "items": item["items"],
                    "fetched_at": str(item.get("fetched_at") or ""),
                }

    def _save_disk(self) -> None:
        path = self._cache_path()
        payload = {
            "version": 1,
            "saved_at": _now_iso(),
            "sources": {
                name: {
                    "items": item.get("items") or [],
                    "fetched_at": item.get("fetched_at") or "",
                }
                for name, item in self._source_cache.items()
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError:
            logger.debug("写入插件目录缓存失败: %s", path, exc_info=True)


def _version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version or "").split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


_CATALOG: CatalogService | None = None


def get_catalog_service() -> CatalogService:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = CatalogService()
    return _CATALOG


__all__ = [
    "CatalogResult",
    "CatalogService",
    "get_catalog_service",
]
