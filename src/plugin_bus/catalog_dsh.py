"""DSH 社区目录适配。

DSH（DeepSeek Harness）基于 Cordis 内核，插件是 npm 包，不能在本系统直接运行。
这里做两件事：

1. 把 npm 搜索 / 包详情结果转成市场展示结构；
2. 给包含 ``SKILL.md`` 的条目补上 ``npm_dist``，供「转译为技能」下载 tarball。

其余 Cordis 插件明确标记 ``unsupported``，不伪装成可安装插件。
"""

from __future__ import annotations

import re
from urllib.parse import quote

DSH_SOURCE = "dsh"
IMPORT_KIND_SKILL = "skill-import"
IMPORT_KIND_NONE = "unsupported"
DEFAULT_QUERY = "awesome-dsh-plugin"
NPM_SCOPE_PATTERN = re.compile(r"^@[^/]+/")


def search_url(registry: str, query: str, size: int = 50) -> str:
    """npm registry 搜索地址；``registry`` 是 registry 根地址。"""
    base = str(registry or "").rstrip("/")
    return f"{base}/-/v1/search?text={quote(str(query or DEFAULT_QUERY))}&size={int(size)}"


def package_url(registry: str, name: str, version: str = "") -> str:
    """npm 包元数据地址；带版本时取指定版本。"""
    base = str(registry or "").rstrip("/")
    cleaned = quote(str(name or ""), safe="@/-._")
    if version:
        return f"{base}/{cleaned}/{quote(str(version), safe='.-+')}"
    return f"{base}/{cleaned}"


def normalize_plugin_id(name: str) -> str:
    """把 npm 包名清洗成 plugin_id。"""
    raw = NPM_SCOPE_PATTERN.sub("", str(name or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:60]
    if not cleaned:
        cleaned = "dsh-plugin"
    if not cleaned[0].isalnum():
        cleaned = f"dsh-{cleaned}"[:64]
    return f"dsh-{cleaned}"[:64]


def _iter_entries(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("objects", "packages", "plugins", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _package_of(entry: dict) -> dict:
    package = entry.get("package")
    return package if isinstance(package, dict) else entry


def _looks_like_skill(entry: dict, package: dict) -> bool:
    keywords = package.get("keywords") or entry.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    words = " ".join(str(item) for item in keywords if item).casefold()
    name = str(package.get("name") or entry.get("name") or "").casefold()
    description = str(package.get("description") or entry.get("description") or "").casefold()
    return (
        "skill" in words
        or "skill" in name
        or "技能" in description
        or "skill" in description
    )


def _author_of(package: dict, entry: dict) -> tuple[str, str]:
    """从 author / publisher / maintainers 里取作者，不伪造作者名。"""
    for source in (package, entry):
        raw = source.get("author")
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("username") or "").strip()
            homepage = str(raw.get("url") or raw.get("homepage") or "").strip()
            if name:
                return name, homepage
        elif isinstance(raw, str) and raw.strip():
            text = raw.strip()
            homepage = ""
            if text.endswith(")") and "(" in text:
                homepage = text[text.rfind("(") + 1 : -1].strip()
                text = text[: text.rfind("(")].strip()
            if "<" in text and ">" in text:
                text = text[: text.find("<")].strip()
            if text:
                return text, homepage
    for source in (package, entry):
        publisher = source.get("publisher")
        if isinstance(publisher, dict):
            name = str(
                publisher.get("username") or publisher.get("name") or ""
            ).strip()
            if name:
                return name, ""
        maintainers = source.get("maintainers")
        if isinstance(maintainers, list):
            for item in maintainers:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("username") or "").strip()
                    if name:
                        return name, ""
                elif isinstance(item, str) and item.strip():
                    return item.strip(), ""
    return "", ""


def apply_detail(entry: dict, detail: object) -> bool:
    """把 npm 包详情里的 tarball / 完整性 / 作者字段写到市场条目。

    返回是否拿到可下载的 tarball。拿不到时条目仍可展示，但不能转译。
    """
    if not isinstance(detail, dict):
        return False
    author = entry.get("author") if isinstance(entry.get("author"), dict) else {}
    if not str(author.get("name") or "").strip():
        name, homepage = _author_of(detail, {})
        if name:
            entry["author"] = {"name": name, "homepage": homepage}
    dist = detail.get("dist")
    if not isinstance(dist, dict):
        return False
    tarball = str(dist.get("tarball") or "").strip()
    entry["npm_dist"] = {
        "tarball": tarball,
        "integrity": str(dist.get("integrity") or "").strip(),
        "shasum": str(dist.get("shasum") or "").strip(),
        "size": int(dist.get("unpackedSize") or 0),
    }
    importable = bool(tarball) and entry.get("import_kind") == IMPORT_KIND_SKILL
    entry["importable"] = importable
    if not importable and entry.get("import_kind") == IMPORT_KIND_SKILL:
        entry["unsupported_reason"] = "该条目未提供可下载的 npm 包，无法转译为技能"
    return bool(tarball)


def map_entry(entry: dict, detail: object | None = None) -> dict | None:
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
    author_name, author_homepage = _author_of(package, entry)
    keywords = package.get("keywords") or entry.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    skill_like = _looks_like_skill(entry, package)
    item: dict = {
        "plugin_id": normalize_plugin_id(name),
        "name": name,
        "version": version,
        "description": description,
        "summary": description[:120],
        "author": {"name": author_name, "homepage": author_homepage},
        "license": str(package.get("license") or ""),
        "tag": [str(item) for item in keywords if item][:10],
        "source": DSH_SOURCE,
        "capabilities": [],
        "permissions": [],
        "repository": repository,
        "dist": {},
        "npm_name": name,
        "npm_dist": {"tarball": "", "integrity": "", "shasum": "", "size": 0},
        "installable": False,
        "importable": False,
        "unsupported": not skill_like,
        "import_kind": IMPORT_KIND_SKILL if skill_like else IMPORT_KIND_NONE,
        "unsupported_reason": (
            "DSH 插件不能直接运行；包含 SKILL.md 时可转译为技能后导入"
            if skill_like
            else "本系统不支持该 DSH Cordis 插件"
        ),
        "like": int(entry.get("score") or 0),
        "downloadCount": 0,
    }
    if detail is not None:
        apply_detail(item, detail)
    return item


def map_entries(payload: object, details: dict[str, object] | None = None) -> list[dict]:
    """把搜索响应映射成列表；``details`` 以 npm 包名为键补详情。"""
    entries: list[dict] = []
    for raw in _iter_entries(payload):
        package = _package_of(raw)
        name = str(package.get("name") or raw.get("name") or "").strip()
        mapped = map_entry(raw, (details or {}).get(name))
        if mapped is not None:
            entries.append(mapped)
    return entries


__all__ = [
    "DSH_SOURCE",
    "IMPORT_KIND_SKILL",
    "DEFAULT_QUERY",
    "search_url",
    "package_url",
    "normalize_plugin_id",
    "apply_detail",
    "map_entry",
    "map_entries",
]
