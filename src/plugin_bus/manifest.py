"""manifest 解析与校验。

manifest 是插件对外的唯一描述：市场展示、安装校验、握手与能力注入都读它，
不再维护第二套字段。解析失败统一抛 ``PluginManifestError``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin_bus import protocol
from plugin_bus.errors import PluginManifestError

PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
PROTOCOL_PATTERN = re.compile(r"^(\d+)\.(\d+)$")

CAPABILITIES = frozenset({"tool", "resource", "prompt", "hook", "ui", "integration"})
PERMISSIONS = frozenset(
    {
        "fs:read",
        "fs:write",
        "net",
        "process",
        "context:read",
        "storage",
        "secret",
        "ui",
    }
)
HOOK_NAMES = frozenset({"before_model", "after_model", "before_tool", "after_tool"})
RUNTIME_KINDS = frozenset({"subprocess", "remote", "inprocess"})
TRANSPORT_KINDS = frozenset({"stdio", "http", "sandbox_stdio", "inprocess"})


def _error(message: str, *, plugin_id: str = "") -> PluginManifestError:
    return PluginManifestError(message, plugin_id=plugin_id)


def parse_semver(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


@dataclass(frozen=True)
class HarnessVersion:
    """宿主版本范围：``min`` 为下界，``max`` 支持 ``1.x`` 形式。"""

    minimum: str = "1.1.0"
    maximum: str = "1.x"

    def matches(self, current: str) -> bool:
        current_version = parse_semver(current)
        if current_version is None:
            return False
        minimum = parse_semver(self.minimum)
        if minimum is not None and current_version < minimum:
            return False
        maximum = str(self.maximum or "").strip().lower()
        if not maximum:
            return True
        if maximum.endswith(".x"):
            try:
                major = int(maximum[:-2])
            except ValueError:
                return False
            return current_version[0] <= major
        maximum_version = parse_semver(maximum)
        if maximum_version is not None and current_version > maximum_version:
            return False
        return True


@dataclass(frozen=True)
class RuntimeSpec:
    kind: str
    language: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    # 进程内内置插件的入口，形如 ``plugin:handle`` 或 ``plugin``。
    entry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "language": self.language,
            "command": self.command,
            "args": list(self.args),
            "entry": self.entry,
        }


@dataclass(frozen=True)
class TransportSpec:
    kind: str
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "url": self.url}


@dataclass(frozen=True)
class UISlot:
    slot: str
    title: str = ""
    entry: str = "ui/index.html"


@dataclass(frozen=True)
class Manifest:
    plugin_id: str
    name: str
    version: str
    protocol_version: str
    description: str
    author_name: str
    author_homepage: str
    license: str
    tags: tuple[str, ...]
    source: str
    capabilities: tuple[str, ...]
    runtime: RuntimeSpec
    transport: TransportSpec
    permissions: tuple[str, ...]
    env: dict[str, str]
    ui_slots: tuple[UISlot, ...]
    harness_version: HarnessVersion
    config_schema: dict[str, Any]
    like: int
    download_count: int
    dist: dict[str, Any]
    changelog: list[Any]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.plugin_id}@{self.version}"

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities

    def has_permission(self, name: str) -> bool:
        return name in self.permissions

    def to_public_dict(self) -> dict[str, Any]:
        """市场卡片与详情共用的展示结构。"""
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "description": self.description,
            "author": {
                "name": self.author_name,
                "homepage": self.author_homepage,
            },
            "license": self.license,
            "tag": list(self.tags),
            "source": self.source,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "runtime": self.runtime.to_dict(),
            "transport": self.transport.to_dict(),
            "env": dict(self.env),
            "ui": {"slots": [slot.__dict__ for slot in self.ui_slots]},
            "harnessVersion": {
                "min": self.harness_version.minimum,
                "max": self.harness_version.maximum,
            },
            "configSchema": self.config_schema,
            "like": self.like,
            "downloadCount": self.download_count,
            "dist": dict(self.dist),
            "changelog": list(self.changelog),
        }


def _as_dict(value: Any, field_name: str, *, plugin_id: str = "") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _error(f"{field_name} 必须是对象", plugin_id=plugin_id)
    return value


def _as_string_list(value: Any, field_name: str, *, plugin_id: str = "") -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _error(f"{field_name} 必须是字符串列表", plugin_id=plugin_id)
    return tuple(item.strip() for item in value if item.strip())


def manifest_from_dict(
    raw: dict[str, Any],
    *,
    source: str = "user",
    harness_version: str | None = None,
    project_level: bool = False,
) -> Manifest:
    """解析并校验 manifest。"""
    if not isinstance(raw, dict):
        raise _error("manifest 必须是 JSON 对象")

    plugin_id = str(raw.get("plugin_id") or "").strip()
    if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise _error("plugin_id 只能包含字母、数字、连字符和下划线，长度 1~64")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise _error("manifest 缺少 name", plugin_id=plugin_id)

    version = str(raw.get("version") or "").strip()
    if parse_semver(version) is None:
        raise _error(f"version 不是合法 semver: {version!r}", plugin_id=plugin_id)

    protocol_version = str(raw.get("protocolVersion") or protocol.PROTOCOL_VERSION)
    if not PROTOCOL_PATTERN.fullmatch(protocol_version):
        raise _error("protocolVersion 必须是 MAJOR.MINOR", plugin_id=plugin_id)
    if not protocol.protocol_compatible(protocol_version):
        raise _error(
            f"protocolVersion 不兼容：插件 {protocol_version}，总线 {protocol.PROTOCOL_VERSION}",
            plugin_id=plugin_id,
        )

    capabilities_raw = _as_string_list(raw.get("capabilities"), "capabilities", plugin_id=plugin_id)
    if not capabilities_raw:
        raise _error("capabilities 不能为空", plugin_id=plugin_id)
    unknown_capabilities = set(capabilities_raw) - CAPABILITIES
    if unknown_capabilities:
        raise _error(
            f"未知能力: {sorted(unknown_capabilities)}", plugin_id=plugin_id
        )

    permissions_raw = _as_string_list(raw.get("permissions"), "permissions", plugin_id=plugin_id)
    unknown_permissions = set(permissions_raw) - PERMISSIONS
    if unknown_permissions:
        raise _error(
            f"未知权限: {sorted(unknown_permissions)}", plugin_id=plugin_id
        )

    runtime_raw = _as_dict(raw.get("runtime"), "runtime", plugin_id=plugin_id)
    runtime_kind = str(runtime_raw.get("kind") or "").strip()
    if runtime_kind not in RUNTIME_KINDS:
        raise _error(
            "runtime.kind 只能是 subprocess / remote / inprocess",
            plugin_id=plugin_id,
        )
    command = str(runtime_raw.get("command") or "").strip()
    args = _as_string_list(runtime_raw.get("args"), "runtime.args", plugin_id=plugin_id)
    entry = str(runtime_raw.get("entry") or "").strip()
    if runtime_kind == "subprocess" and not command:
        raise _error("subprocess 插件缺少 runtime.command", plugin_id=plugin_id)
    if runtime_kind == "inprocess":
        if source != "builtin":
            raise _error(
                "runtime.kind=inprocess 只允许系统内置插件使用",
                plugin_id=plugin_id,
            )
        if project_level:
            raise _error(
                "项目级插件不允许 inprocess，必须在沙箱运行", plugin_id=plugin_id
            )
        if not entry:
            raise _error(
                "inprocess 插件缺少 runtime.entry（如 plugin:handle）",
                plugin_id=plugin_id,
            )
    runtime = RuntimeSpec(
        kind=runtime_kind,
        language=str(runtime_raw.get("language") or "").strip(),
        command=command,
        args=args,
        entry=entry,
    )

    transport_raw = _as_dict(raw.get("transport"), "transport", plugin_id=plugin_id)
    transport_kind = str(transport_raw.get("kind") or "").strip()
    if not transport_kind:
        if runtime_kind == "remote":
            transport_kind = "http"
        elif runtime_kind == "inprocess":
            transport_kind = "inprocess"
        else:
            transport_kind = "stdio"
    if transport_kind not in TRANSPORT_KINDS:
        raise _error(
            "transport.kind 只能是 stdio / http / sandbox_stdio / inprocess",
            plugin_id=plugin_id,
        )
    if runtime_kind == "inprocess":
        # 进程内插件没有真实传输层，统一标记为 inprocess。
        transport_kind = "inprocess"
    if project_level:
        # 项目级插件只能在沙箱内运行，不接受 manifest 指定宿主传输。
        transport_kind = "sandbox_stdio"
    transport_url = str(transport_raw.get("url") or "").strip()
    if runtime_kind == "remote":
        if transport_kind != "http":
            raise _error("远程插件必须使用 http 传输", plugin_id=plugin_id)
        if not transport_url.startswith(("http://", "https://")):
            raise _error("远程插件缺少合法 transport.url", plugin_id=plugin_id)
    transport = TransportSpec(kind=transport_kind, url=transport_url)

    ui_raw = _as_dict(raw.get("ui"), "ui", plugin_id=plugin_id)
    slots_raw = ui_raw.get("slots") or []
    if not isinstance(slots_raw, list):
        raise _error("ui.slots 必须是数组", plugin_id=plugin_id)
    slots: list[UISlot] = []
    for item in slots_raw:
        if not isinstance(item, dict):
            raise _error("ui.slots 条目必须是对象", plugin_id=plugin_id)
        slot_name = str(item.get("slot") or "").strip()
        if not slot_name:
            raise _error("ui.slots 缺少 slot", plugin_id=plugin_id)
        entry = str(item.get("entry") or "ui/index.html").strip()
        if entry.startswith(("/", "\\")) or ".." in entry.split("/"):
            raise _error(f"ui.entry 必须是包内相对路径: {entry}", plugin_id=plugin_id)
        slots.append(
            UISlot(
                slot=slot_name,
                title=str(item.get("title") or "").strip(),
                entry=entry,
            )
        )
    if "ui" in capabilities_raw and not slots:
        raise _error("声明 ui 能力时必须提供 ui.slots", plugin_id=plugin_id)
    if "ui" in capabilities_raw and "ui" not in permissions_raw:
        raise _error("声明 ui 能力时必须同时声明 ui 权限", plugin_id=plugin_id)

    harness_raw = _as_dict(raw.get("harnessVersion"), "harnessVersion", plugin_id=plugin_id)
    harness = HarnessVersion(
        minimum=str(harness_raw.get("min") or "1.1.0").strip(),
        maximum=str(harness_raw.get("max") or "1.x").strip(),
    )
    current_version = harness_version
    if current_version is None:
        from agent.config import HARNESS_VERSION

        current_version = HARNESS_VERSION
    if not harness.matches(current_version):
        raise _error(
            f"插件要求 harnessVersion {harness.minimum} ~ {harness.maximum}，"
            f"当前宿主 {current_version}",
            plugin_id=plugin_id,
        )

    author_raw = _as_dict(raw.get("author"), "author", plugin_id=plugin_id)
    author_name = str(author_raw.get("name") or "").strip()
    if not author_name:
        raise _error("manifest 必须提供 author.name", plugin_id=plugin_id)

    config_schema = _as_dict(raw.get("configSchema"), "configSchema", plugin_id=plugin_id)
    env_raw = _as_dict(raw.get("env"), "env", plugin_id=plugin_id)
    env = {str(key): str(value) for key, value in env_raw.items()}

    dist = _as_dict(raw.get("dist"), "dist", plugin_id=plugin_id)
    changelog = raw.get("changelog") or []
    if not isinstance(changelog, list):
        raise _error("changelog 必须是数组", plugin_id=plugin_id)

    return Manifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        protocol_version=protocol_version,
        description=str(raw.get("description") or "").strip(),
        author_name=author_name,
        author_homepage=str(author_raw.get("homepage") or "").strip(),
        license=str(raw.get("license") or "").strip(),
        tags=_as_string_list(raw.get("tag"), "tag", plugin_id=plugin_id),
        source=str(raw.get("source") or source).strip() or source,
        capabilities=capabilities_raw,
        runtime=runtime,
        transport=transport,
        permissions=permissions_raw,
        env=env,
        ui_slots=tuple(slots),
        harness_version=harness,
        config_schema=config_schema,
        like=int(raw.get("like") or 0),
        download_count=int(raw.get("downloadCount") or 0),
        dist=dist,
        changelog=changelog,
        raw=dict(raw),
    )


def load_manifest(
    path: str | Path,
    *,
    source: str = "user",
    harness_version: str | None = None,
    project_level: bool = False,
) -> Manifest:
    """从 ``manifest.json`` 读取；缺失或 JSON 错误统一转成 manifest 错误。"""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise _error(f"缺少 manifest.json: {manifest_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise _error(f"manifest.json 读取失败: {error}") from error
    if not isinstance(raw, dict):
        raise _error("manifest.json 必须是 JSON 对象")
    return manifest_from_dict(
        raw,
        source=source,
        harness_version=harness_version,
        project_level=project_level,
    )


__all__ = [
    "Manifest",
    "RuntimeSpec",
    "TransportSpec",
    "UISlot",
    "HarnessVersion",
    "CAPABILITIES",
    "PERMISSIONS",
    "HOOK_NAMES",
    "PLUGIN_ID_PATTERN",
    "manifest_from_dict",
    "load_manifest",
    "parse_semver",
]
