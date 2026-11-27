"""插件能力注册表。

把手握得到的 ``plugin.describe`` 转成 Agent 可用的工具代理、资源目录、
提示词与 hook 清单；同时负责命名冲突检测与快照持久化。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from plugin_bus import protocol
from plugin_bus.errors import PluginError
from plugin_bus.manifest import HOOK_NAMES, Manifest

logger = logging.getLogger(__name__)

TOOL_PREFIX = "plugin__"
MAX_TOOL_NAME_LENGTH = 64


def _json_schema_type(schema: dict[str, Any]) -> Any:
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return str
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "string")
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return list[_json_schema_type(item_schema)]  # type: ignore[valid-type]
        return list[Any]
    if schema_type == "object":
        return dict[str, Any]
    if schema_type == "string":
        return str
    return Any


def build_args_schema(input_schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    """把工具 inputSchema 转成 LangChain 可用的 Pydantic 参数模型。"""
    properties = input_schema.get("properties")
    required = set(input_schema.get("required") or [])
    fields: dict[str, Any] = {}
    if isinstance(properties, dict):
        for name, raw_schema in properties.items():
            schema = raw_schema if isinstance(raw_schema, dict) else {}
            python_type = _json_schema_type(schema)
            description = str(schema.get("description", ""))
            default = schema.get("default")
            if name in required:
                fields[str(name)] = (python_type, Field(..., description=description))
            else:
                fields[str(name)] = (python_type, Field(default, description=description))
    safe_name = "".join(
        char if char.isalnum() or char == "_" else "_" for char in model_name
    )
    return create_model(  # type: ignore[call-overload]
        safe_name or "PluginToolArgs",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


class PluginProxyTool(BaseTool):
    """指向 Plugin Runtime 的稳定代理工具。"""

    user_id: str
    plugin_id: str
    remote_name: str

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("插件工具只支持异步调用")

    async def _arun(self, **kwargs: Any) -> Any:
        from plugin_bus.runtime import get_plugin_runtime

        return await get_plugin_runtime(self.user_id).call_tool(
            self.plugin_id, self.remote_name, kwargs
        )


@dataclass
class ToolMeta:
    plugin_id: str
    remote_name: str
    exposed_name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.remote_name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class CapabilitySnapshot:
    plugin_id: str
    version: str
    manifest: Manifest
    tools: list[ToolMeta] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    ui_slots: list[dict[str, Any]] = field(default_factory=list)
    describe: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "manifest": self.manifest.raw,
            "tools": [item.to_dict() for item in self.tools],
            "resources": self.resources,
            "prompts": self.prompts,
            "hooks": self.hooks,
            "ui_slots": self.ui_slots,
            "describe": self.describe,
        }


def _capability_items(describe: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = (describe.get("capabilities") or {}).get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise PluginError(
            f"describe.capabilities.{name} 必须是数组",
            code="PLUGIN_CAPABILITY_MISMATCH",
            http_status=422,
        )
    return [item for item in value if isinstance(item, dict)]


def build_snapshot(manifest: Manifest, describe: dict[str, Any]) -> CapabilitySnapshot:
    """校验 describe 与 manifest 的一致性，并转成注册表快照。"""
    reported = describe.get("capabilities")
    if not isinstance(reported, dict):
        raise PluginError(
            "plugin.describe 缺少 capabilities",
            code="PLUGIN_CAPABILITY_MISMATCH",
            http_status=422,
            plugin_id=manifest.plugin_id,
        )
    declared = set(manifest.capabilities)
    reported_types = {name for name, value in reported.items() if value}
    unknown = reported_types - declared
    if unknown:
        raise PluginError(
            f"describe 上报了未声明能力: {sorted(unknown)}",
            code="PLUGIN_CAPABILITY_MISMATCH",
            http_status=422,
            plugin_id=manifest.plugin_id,
        )

    tools: list[ToolMeta] = []
    for item in _capability_items(describe, "tool"):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        exposed = f"{TOOL_PREFIX}{manifest.plugin_id}__{name}"
        tools.append(
            ToolMeta(
                plugin_id=manifest.plugin_id,
                remote_name=name,
                exposed_name=exposed[:MAX_TOOL_NAME_LENGTH],
                description=str(item.get("description") or "").strip(),
                input_schema=item.get("inputSchema")
                if isinstance(item.get("inputSchema"), dict)
                else {},
            )
        )

    hooks: list[dict[str, Any]] = []
    for item in _capability_items(describe, "hook"):
        name = str(item.get("name") or "").strip()
        if name not in HOOK_NAMES:
            raise PluginError(
                f"未知 hook: {name!r}",
                code="PLUGIN_CAPABILITY_MISMATCH",
                http_status=422,
                plugin_id=manifest.plugin_id,
            )
        hooks.append(
            {
                "plugin_id": manifest.plugin_id,
                "name": name,
                "priority": int(item.get("priority") or 100),
            }
        )

    return CapabilitySnapshot(
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        manifest=manifest,
        tools=tools,
        resources=_capability_items(describe, "resource"),
        prompts=_capability_items(describe, "prompt"),
        hooks=hooks,
        ui_slots=_capability_items(describe, "ui"),
        describe=describe,
    )


def _cache_path(user_id: str) -> Path:
    from agent.config import HARNESS_HOME
    from agent.identity import safe_user_id

    return (
        Path(HARNESS_HOME)
        / "cache"
        / "index"
        / f"plugins-capabilities-{safe_user_id(user_id)}.json"
    )


class CapabilityRegistry:
    """按用户隔离的能力注册表。"""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], CapabilitySnapshot] = {}
        self._proxies: dict[tuple[str, str], PluginProxyTool] = {}
        self._conflicts: dict[str, str] = {}

    # ---------- 注册 ----------

    def register(
        self,
        user_id: str,
        snapshot: CapabilitySnapshot,
        *,
        reserved_names: set[str] | None = None,
    ) -> bool:
        reserved = reserved_names or set()
        before = {tool.exposed_name for tool in self._snapshot_tools(user_id)}
        self.remove(user_id, snapshot.plugin_id, keep_proxies=True)
        used = {tool.exposed_name for tool in self._snapshot_tools(user_id)}
        valid_tools: list[ToolMeta] = []
        for meta in snapshot.tools:
            if len(meta.exposed_name) > MAX_TOOL_NAME_LENGTH:
                self._conflicts[meta.exposed_name] = "工具名超过长度限制"
                continue
            if meta.exposed_name in used or meta.exposed_name in reserved:
                self._conflicts[meta.exposed_name] = (
                    f"工具名冲突，已跳过（来源 {snapshot.plugin_id}）"
                )
                continue
            used.add(meta.exposed_name)
            valid_tools.append(meta)
        snapshot.tools = valid_tools
        self._snapshots[(user_id, snapshot.plugin_id)] = snapshot
        after = {tool.exposed_name for tool in self._snapshot_tools(user_id)}
        return before != after

    def remove(self, user_id: str, plugin_id: str, *, keep_proxies: bool = False) -> None:
        key = (user_id, plugin_id)
        self._snapshots.pop(key, None)
        if keep_proxies:
            return
        for proxy_key in [
            item for item in self._proxies if item[0] == user_id
        ]:
            self._proxies.pop(proxy_key, None)

    def clear_user(self, user_id: str) -> None:
        for key in [item for item in self._snapshots if item[0] == user_id]:
            self._snapshots.pop(key, None)
        for key in [item for item in self._proxies if item[0] == user_id]:
            self._proxies.pop(key, None)

    # ---------- 查询 ----------

    def snapshots(self, user_id: str) -> dict[str, CapabilitySnapshot]:
        return {
            plugin_id: snapshot
            for (uid, plugin_id), snapshot in self._snapshots.items()
            if uid == user_id
        }

    def snapshot(self, user_id: str, plugin_id: str) -> CapabilitySnapshot | None:
        return self._snapshots.get((user_id, plugin_id))

    def _snapshot_tools(self, user_id: str) -> list[ToolMeta]:
        tools: list[ToolMeta] = []
        for snapshot in self.snapshots(user_id).values():
            tools.extend(snapshot.tools)
        return tools

    def conflicts(self) -> dict[str, str]:
        return dict(self._conflicts)

    def tools_for_user(
        self, user_id: str, *, reserved_names: set[str] | None = None
    ) -> list[BaseTool]:
        reserved = reserved_names or set()
        result: list[BaseTool] = []
        for snapshot in self.snapshots(user_id).values():
            for meta in snapshot.tools:
                if meta.exposed_name in reserved:
                    continue
                key = (user_id, meta.exposed_name)
                proxy = self._proxies.get(key)
                if proxy is None:
                    proxy = PluginProxyTool(
                        name=meta.exposed_name,
                        description="",
                        args_schema=build_args_schema(
                            meta.input_schema, meta.exposed_name
                        ),
                        user_id=user_id,
                        plugin_id=meta.plugin_id,
                        remote_name=meta.remote_name,
                        handle_tool_error=True,
                    )
                    self._proxies[key] = proxy
                proxy.description = (
                    f"[插件 {snapshot.manifest.name}: {snapshot.plugin_id}]\n"
                    f"{meta.description}"
                ).strip()
                proxy.args_schema = build_args_schema(
                    meta.input_schema, meta.exposed_name
                )
                result.append(proxy)
        return result

    def prompts_for_user(
        self, user_id: str, message_text: str = ""
    ) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        lowered = message_text.casefold()
        for snapshot in self.snapshots(user_id).values():
            for prompt in snapshot.prompts:
                trigger = str(prompt.get("trigger") or "always").lower()
                if trigger == "keyword":
                    keywords = [
                        str(item).casefold()
                        for item in (prompt.get("keywords") or [])
                        if str(item).strip()
                    ]
                    if not keywords or not any(word in lowered for word in keywords):
                        continue
                elif trigger not in {"always", "capability"}:
                    continue
                accepted.append({**prompt, "plugin_id": snapshot.plugin_id})
        return accepted

    def resource_catalog(self, user_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for snapshot in self.snapshots(user_id).values():
            for resource in snapshot.resources:
                items.append(
                    {
                        "plugin_id": snapshot.plugin_id,
                        "uri": str(resource.get("uri") or ""),
                        "name": str(resource.get("name") or ""),
                        "mimeType": str(resource.get("mimeType") or ""),
                    }
                )
        return items

    def hooks_for_user(self, user_id: str, name: str) -> list[dict[str, Any]]:
        hooks: list[dict[str, Any]] = []
        for snapshot in self.snapshots(user_id).values():
            for hook in snapshot.hooks:
                if hook["name"] == name:
                    hooks.append(hook)
        hooks.sort(key=lambda item: int(item.get("priority") or 100))
        return hooks

    # ---------- 快照持久化 ----------

    def save(self, user_id: str) -> None:
        payload = {
            "version": 1,
            "snapshots": [
                snapshot.to_dict() for snapshot in self.snapshots(user_id).values()
            ],
        }
        path = _cache_path(user_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError:
            logger.debug("写入插件能力快照失败: %s", path, exc_info=True)

    def load(self, user_id: str, project_root: Path | None = None) -> None:
        """从磁盘快照恢复；manifest 失效时跳过，不影响启动。"""
        from plugin_bus.manifest import manifest_from_dict

        path = _cache_path(user_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except (OSError, json.JSONDecodeError):
            return
        items = payload.get("snapshots") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_manifest = item.get("manifest")
            describe = item.get("describe")
            if not isinstance(raw_manifest, dict) or not isinstance(describe, dict):
                continue
            try:
                manifest = manifest_from_dict(
                    raw_manifest, source=str(raw_manifest.get("source") or "user")
                )
                snapshot = build_snapshot(manifest, describe)
            except PluginError:
                continue
            except Exception:
                continue
            self.register(user_id, snapshot)


__all__ = [
    "CapabilityRegistry",
    "CapabilitySnapshot",
    "ToolMeta",
    "PluginProxyTool",
    "build_args_schema",
    "build_snapshot",
    "TOOL_PREFIX",
]
