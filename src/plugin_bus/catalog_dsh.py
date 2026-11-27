"""DSH 社区目录只读适配。

DSH（DeepSeek Harness）基于 Cordis 内核，插件不能直接在本系统运行。这里只做
「转译导入」：能识别到 SKILL 内容的标记为可转译技能，其余明确标记不支持，
不伪装成可安装插件。
"""

from __future__ import annotations

import re
from typing import Any

DSH_SOURCE = "dsh"
NPM_SCOPE_PATTERN = re.compile(r"^@[^/]+/")


def normalize_plugin_id(name: str) -> str:
    """把 npm 包名清洗成 plugin_id。"""
    raw = NPM_SCOPE_PATTERN.sub("", str(name or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:60]
    if not cleaned:
        cleaned = "dsh-plugin"
    if not cleaned[0].isalnum():
        cleaned = f"dsh-{cleaned}"[:64]
    return f"dsh-{cleaned}"[:64]


def _iter_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("objects", "packages", "plugins", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _package_of(entry: dict[str, Any]) -> dict[str, Any]:
    package = entry.get("package")
    return package if isinstance(package, dict) else entry


def _looks_like_skill(entry: dict[str, Any], package: dict[str, Any]) -> bool:
    keywords = package.get("keywords") or entry.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    words = " ".join(str(item) for item in keywords if item).casefold()
    name = str(package.get("name") or entry.get("name") or "").casefold()
    description = str(package.get("description") or entry.get("description") or "").casefold()
    return "skill" in words or "skill" in name or "技能" in description


def map_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """把 npm / DSH 目录条目映射成市场展示结构。"""
    package = _package_of(entry)
    name = str(package.get("name") or entry.get("name") or "").strip()
    version = str(package.get("version") or entry.get("version") or "").strip()
    if not name or not version:
        return None
    description = str(
        package.get("description") or entry.get("description") or ""
    ).strip()
    links = package.get("links") if isinstance(package.get("links"), dict) else {}
    repository = (
        str(links.get("repository") or links.get("homepage") or "")
        or str(entry.get("repository") or "")
    )
    author_raw = package.get("author") or entry.get("author") or {}
    if isinstance(author_raw, dict):
        author_name = str(author_raw.get("name") or "").strip()
        author_homepage = str(author_raw.get("url") or "").strip()
    else:
        author_name = str(author_raw or "").strip()
        author_homepage = ""
    keywords = package.get("keywords") or entry.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    skill_like = _looks_like_skill(entry, package)
    return {
        "plugin_id": normalize_plugin_id(name),
        "name": name,
        "version": version,
        "description": description,
        "summary": description[:120],
        "author": {"name": author_name or "dsh-community", "homepage": author_homepage},
        "license": str(package.get("license") or ""),
        "tag": [str(item) for item in keywords if item][:10],
        "source": DSH_SOURCE,
        "capabilities": [],
        "permissions": [],
        "repository": repository,
        "dist": {},
        "installable": False,
        "unsupported": not skill_like,
        "import_kind": "skill-import" if skill_like else "unsupported",
        "unsupported_reason": (
            "DSH Cordis 插件不能直接运行；可提取 SKILL 内容后按技能导入"
            if skill_like
            else "本系统不支持该 DSH Cordis 插件"
        ),
        "like": int(entry.get("score") or 0),
        "downloadCount": 0,
    }


def map_entries(payload: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in _iter_entries(payload):
        mapped = map_entry(raw)
        if mapped is not None:
            entries.append(mapped)
    return entries


__all__ = ["DSH_SOURCE", "normalize_plugin_id", "map_entry", "map_entries"]
