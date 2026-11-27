"""插件包目录与版本指针。

磁盘是插件包内容与版本的权威源；PG 只存安装记录、启停状态、配置与审计。
本模块只做路径解析、原子读写与包解压，不接触数据库。
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from plugin_bus.errors import PluginConflict, PluginPackageInvalid
from plugin_bus.manifest import Manifest, load_manifest, parse_semver

logger = logging.getLogger(__name__)

MAX_ARCHIVE_FILES = 5000

# 随代码发布的内置插件源码根：只读、进程内运行，不复制到用户目录。
SYS_PLUGINS_ROOT = Path(__file__).resolve().parent / "sys_plugins"


def safe_component(value: str, fallback: str = "plugin") -> str:
    """把插件 ID / 版本号清洗成安全的目录名。"""
    cleaned = "".join(
        char for char in str(value or "") if char.isalnum() or char in {"-", "_", "."}
    )[:96]
    return cleaned or fallback


def plugin_dir(user_id: str, plugin_id: str) -> Path:
    from agent.config import plugin_user_root

    return plugin_user_root(user_id) / safe_component(plugin_id)


def versions_dir(user_id: str, plugin_id: str) -> Path:
    return plugin_dir(user_id, plugin_id) / "versions"


def version_dir(user_id: str, plugin_id: str, version: str) -> Path:
    return versions_dir(user_id, plugin_id) / safe_component(version, "0.0.0")


def data_dir(user_id: str, plugin_id: str) -> Path:
    return plugin_dir(user_id, plugin_id) / "data"


def logs_dir(user_id: str, plugin_id: str) -> Path:
    return plugin_dir(user_id, plugin_id) / "logs"


def stdio_log_path(user_id: str, plugin_id: str) -> Path:
    return logs_dir(user_id, plugin_id) / "stdio.log"


def current_path(user_id: str, plugin_id: str) -> Path:
    return plugin_dir(user_id, plugin_id) / "current.json"


def ensure_plugin_dirs(user_id: str, plugin_id: str, version: str) -> None:
    """创建版本目录、data 与 logs；失败直接抛出，由调用方回滚。"""
    version_dir(user_id, plugin_id, version).mkdir(parents=True, exist_ok=True)
    data_dir(user_id, plugin_id).mkdir(parents=True, exist_ok=True)
    logs_dir(user_id, plugin_id).mkdir(parents=True, exist_ok=True)


def read_current(user_id: str, plugin_id: str) -> str:
    path = current_path(user_id, plugin_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, json.JSONDecodeError):
        return ""
    version = payload.get("version") if isinstance(payload, dict) else ""
    return str(version or "").strip()


def write_current(user_id: str, plugin_id: str, version: str) -> None:
    """原子切换版本指针，失败不破坏原文件。"""
    path = current_path(user_id, plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": version}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".current.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def list_versions(user_id: str, plugin_id: str) -> list[str]:
    root = versions_dir(user_id, plugin_id)
    if not root.is_dir():
        return []
    versions = [
        child.name
        for child in root.iterdir()
        if child.is_dir() and parse_semver(child.name) is not None
    ]
    versions.sort(key=lambda item: parse_semver(item) or (0, 0, 0))
    return versions


def latest_version(user_id: str, plugin_id: str) -> str:
    versions = list_versions(user_id, plugin_id)
    return versions[-1] if versions else ""


@dataclass(frozen=True)
class InstalledPlugin:
    plugin_id: str
    version: str
    path: Path
    source: str
    manifest: Manifest

    def summary(self) -> dict[str, object]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "path": str(self.path),
            "source": self.source,
        }


def _load_from_version_dir(
    directory: Path, *, source: str, project_level: bool = False
) -> InstalledPlugin | None:
    try:
        manifest = load_manifest(
            directory / "manifest.json",
            source=source,
            project_level=project_level,
        )
    except Exception:
        logger.debug("跳过无效插件版本目录: %s", directory, exc_info=True)
        return None
    return InstalledPlugin(
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        path=directory,
        source=source,
        manifest=manifest,
    )


def discover_user_plugins(user_id: str) -> dict[str, InstalledPlugin]:
    """扫描用户目录：以 ``current.json`` 指向的版本为准。"""
    from agent.config import plugin_user_root

    root = plugin_user_root(user_id)
    found: dict[str, InstalledPlugin] = {}
    if not root.is_dir():
        return found
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        plugin_id = child.name
        version = read_current(user_id, plugin_id)
        if not version:
            versions = list_versions(user_id, plugin_id)
            version = versions[-1] if versions else ""
        if not version:
            continue
        directory = version_dir(user_id, plugin_id, version)
        item = _load_from_version_dir(directory, source="user")
        if item is not None:
            found[plugin_id] = item
    return found


def discover_sys_plugins() -> dict[str, InstalledPlugin]:
    """扫描随包的进程内内置插件：``sys_plugins/{plugin_id}/manifest.json``。"""
    found: dict[str, InstalledPlugin] = {}
    if not SYS_PLUGINS_ROOT.is_dir():
        return found
    for child in SYS_PLUGINS_ROOT.iterdir():
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if not (child / "manifest.json").is_file():
            continue
        item = _load_from_version_dir(child, source="builtin")
        if item is not None:
            found[item.plugin_id] = item
    return found


def discover_builtin_plugins() -> dict[str, InstalledPlugin]:
    """扫描内置目录：版本化部署目录 + 随包 sys_plugins，后者优先。"""
    from agent.config import PLUGIN_BUILTIN_ROOT

    found: dict[str, InstalledPlugin] = {}
    if PLUGIN_BUILTIN_ROOT.is_dir():
        for plugin_path in PLUGIN_BUILTIN_ROOT.iterdir():
            if not plugin_path.is_dir():
                continue
            versions = [
                child
                for child in plugin_path.iterdir()
                if child.is_dir() and parse_semver(child.name) is not None
            ]
            versions.sort(key=lambda item: parse_semver(item.name) or (0, 0, 0))
            if not versions:
                continue
            item = _load_from_version_dir(versions[-1], source="builtin")
            if item is not None:
                found[item.plugin_id] = item
    found.update(discover_sys_plugins())
    return found


def discover_project_plugins(project_root: Path | None) -> dict[str, InstalledPlugin]:
    """项目级插件：``<project>/.commonharness/plugins/{plugin_id}``。"""
    found: dict[str, InstalledPlugin] = {}
    if project_root is None:
        return found
    root = Path(project_root) / ".commonharness" / "plugins"
    if not root.is_dir():
        return found
    for child in root.iterdir():
        if not child.is_dir():
            continue
        item = _load_from_version_dir(child, source="project", project_level=True)
        if item is not None:
            found[item.plugin_id] = item
    return found


def discover_installed(
    user_id: str, project_root: Path | None = None
) -> dict[str, InstalledPlugin]:
    """按 项目级 > 用户级 > 系统内置 的覆盖顺序返回已安装插件。"""
    merged: dict[str, InstalledPlugin] = {}
    merged.update(discover_builtin_plugins())
    merged.update(discover_user_plugins(user_id))
    merged.update(discover_project_plugins(project_root))
    return merged


def remove_plugin_versions(user_id: str, plugin_id: str) -> None:
    """卸载时删除版本目录，保留 data 与 logs。"""
    root = versions_dir(user_id, plugin_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=False)
    current_path(user_id, plugin_id).unlink(missing_ok=True)


def usage(root: Path) -> tuple[int, int]:
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
                stat_result = (Path(dirpath) / filename).stat()
            except OSError:
                continue
            files += 1
            size += stat_result.st_size
    return files, size


def check_quota(user_id: str, *, max_bytes: int, max_plugins: int, adding: bool) -> None:
    from agent.config import plugin_user_root

    root = plugin_user_root(user_id)
    _, size = usage(root)
    if size > max_bytes:
        raise PluginConflict(
            f"用户插件目录超过配额（{size // (1024 * 1024)} MB），"
            f"上限 {max_bytes // (1024 * 1024)} MB"
        )
    if adding:
        count = len(discover_user_plugins(user_id))
        if count >= max_plugins:
            raise PluginConflict(f"插件数量超过上限 {max_plugins} 个")


def safe_extract_zip(data: bytes, destination: Path, *, max_bytes: int) -> Path:
    """校验并解压 ZIP，返回包含 manifest.json 的包根目录。"""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise PluginPackageInvalid(f"不是合法的 ZIP 包: {error}") from error

    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise PluginPackageInvalid(
                f"包内文件过多（{len(members)}），上限 {MAX_ARCHIVE_FILES}"
            )
        total = sum(member.file_size for member in members)
        if total > max_bytes:
            raise PluginPackageInvalid(
                f"包解压后超过 {max_bytes // (1024 * 1024)} MB 限制"
            )
        for member in members:
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or any(part == ".." for part in name.split("/")):
                raise PluginPackageInvalid(f"包内存在非法路径: {member.filename}")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PluginPackageInvalid(f"包内不允许符号链接: {member.filename}")
        archive.extractall(destination)

    root = find_package_root(destination)
    if root is None:
        raise PluginPackageInvalid("包内没有找到 manifest.json")
    return root


def find_package_root(extracted: Path) -> Path | None:
    """兼容「根目录直接放 manifest」与「单层目录包裹」两种包结构。"""
    if (extracted / "manifest.json").is_file():
        return extracted
    candidates = [
        child
        for child in extracted.iterdir()
        if child.is_dir() and (child / "manifest.json").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_ui_file(
    plugin_path: Path, entry: str
) -> Path | None:
    """解析 UI 静态文件，禁止路径穿越。"""
    relative = str(entry or "").replace("\\", "/").lstrip("/")
    parts = relative.split("/")
    if not relative or any(part in {"", ".", ".."} for part in parts):
        return None
    candidate = (plugin_path / relative).resolve()
    try:
        candidate.relative_to(plugin_path.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def iter_package_files(root: Path) -> Iterable[tuple[Path, bytes]]:
    """遍历包内文件，供沙箱上传使用。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".pyc"):
                continue
            path = Path(dirpath) / filename
            try:
                yield path, path.read_bytes()
            except OSError:
                logger.debug("读取插件文件失败: %s", path, exc_info=True)


__all__ = [
    "InstalledPlugin",
    "safe_component",
    "plugin_dir",
    "versions_dir",
    "version_dir",
    "data_dir",
    "logs_dir",
    "stdio_log_path",
    "current_path",
    "ensure_plugin_dirs",
    "read_current",
    "write_current",
    "list_versions",
    "latest_version",
    "discover_user_plugins",
    "discover_sys_plugins",
    "discover_builtin_plugins",
    "discover_project_plugins",
    "discover_installed",
    "remove_plugin_versions",
    "usage",
    "check_quota",
    "safe_extract_zip",
    "find_package_root",
    "resolve_ui_file",
    "iter_package_files",
]
