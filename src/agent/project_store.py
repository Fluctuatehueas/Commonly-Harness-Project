"""项目登记表：按用户隔离的本机项目目录增删改查与启动迁移。

项目登记持久化在 ``USER_DATA_DIR/users/{user_id}/projects.json``；项目目录内的
``.commonharness/project.json`` 才是信任标记。登记表提供快速列表，
标记提供目录级信任证明，两者通过 ``project_id`` 关联。

旧版本把全局工作区根和项目列表混存在机器级 ``workspace.json``；首次启动时
把 ``anonymous`` 用户的旧配置迁移到 ``users/anonymous/``，旧文件保留为 ``.bak``。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import USER_DATA_DIR
from agent.project_audit import append_audit
from agent.project_context import ProjectContext
from agent.project_marker import (
    MARKER_SCHEMA_VERSION,
    ProjectPathError,
    create_marker,
    ensure_directory,
    is_forbidden_root,
    marker_state,
    read_marker,
    validate_directory,
)

DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_NAME = "默认工作区"
PROJECTS_SCHEMA_VERSION = 2
LEGACY_CONFIG_PATH = USER_DATA_DIR / "workspace.json"
MACHINE_PROJECTS_PATH = USER_DATA_DIR / "projects.json"

_lock = threading.RLock()
_states: dict[str, dict[str, Any]] = {}


# ============================================================
# 路径与内部工具
# ============================================================


def _safe_user_id(user_id: str) -> str:
    safe = "".join(
        char for char in str(user_id or "") if char.isalnum() or char in {"-", "_"}
    )[:64]
    return safe or "anonymous"


def projects_config_path(user_id: str) -> Path:
    """用户级项目登记文件。"""
    return USER_DATA_DIR / "users" / _safe_user_id(user_id) / "projects.json"


def default_project_root(user_id: str) -> Path:
    """用户级默认项目目录。"""
    return (
        USER_DATA_DIR / "users" / _safe_user_id(user_id) / "workspaces" / "default"
    ).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_record(item: Any) -> dict[str, Any] | None:
    """把磁盘上的项目记录规范化为内部结构；无法使用时返回 None。"""
    if not isinstance(item, dict):
        return None
    project_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    path_text = str(item.get("path") or "").strip()
    if not project_id or not name or not path_text:
        return None
    try:
        path = Path(path_text).expanduser().resolve()
    except (OSError, ValueError):
        return None
    return {
        "id": project_id,
        "name": name,
        "path": str(path),
        "pinned": bool(item.get("pinned", False)),
        "created_at": str(item.get("created_at") or _now()),
        "updated_at": str(item.get("updated_at") or _now()),
        "last_opened_at": str(item.get("last_opened_at") or ""),
        "marker_state": str(item.get("marker_state") or "missing"),
        "status": str(item.get("status") or "untrusted"),
    }


def _refresh_record(record: dict[str, Any]) -> dict[str, Any]:
    """按当前磁盘状态刷新项目可用性；不做写探针，避免列表接口频繁 IO。"""
    root = Path(record["path"])
    project_id = record["id"]
    if not root.exists() or not root.is_dir():
        record["marker_state"] = "missing"
        record["status"] = "unavailable"
        return record
    state, _detail = marker_state(root, project_id)
    record["marker_state"] = state
    record["status"] = "active" if state == "ok" else "untrusted"
    return record


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(user_id: str, records: list[dict[str, Any]]) -> None:
    """原子写入当前用户的 projects.json，并刷新进程内缓存。"""
    path = projects_config_path(user_id)
    payload = {
        "schema_version": PROJECTS_SCHEMA_VERSION,
        "updated_at": _now(),
        "projects": records,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except OSError:
        _states.pop(user_id, None)
        raise
    _states[user_id] = payload


def _make_record(
    project_id: str, name: str, root: Path, *, created_at: str | None = None,
) -> dict[str, Any]:
    record = {
        "id": project_id,
        "name": name,
        "path": str(root.resolve()),
        "pinned": False,
        "created_at": created_at or _now(),
        "updated_at": _now(),
        "last_opened_at": "",
        "marker_state": "missing",
        "status": "untrusted",
    }
    return _refresh_record(record)


def _legacy_default_root(legacy: dict[str, Any], user_id: str) -> Path:
    """迁移时优先沿用旧全局工作区根，保留已有产物。"""
    raw = str(legacy.get("root") or "").strip()
    if raw:
        try:
            candidate = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            candidate = None
        if candidate and candidate.exists() and candidate.is_dir():
            if not is_forbidden_root(candidate):
                return candidate
    return default_project_root(user_id)


def _ensure_default(
    records: list[dict[str, Any]], legacy: dict[str, Any], user_id: str,
) -> bool:
    """确保当前用户的默认项目存在；返回是否新增记录。"""
    existing = next(
        (item for item in records if item["id"] == DEFAULT_PROJECT_ID), None
    )
    if existing is not None:
        root = Path(existing["path"])
        if root.exists() and marker_state(root, DEFAULT_PROJECT_ID)[0] == "missing":
            try:
                create_marker(root, project_id=DEFAULT_PROJECT_ID, name=existing["name"])
            except ProjectPathError as error:
                logger.warning("默认项目标记修复失败：%s", error.message)
        return False

    root = _legacy_default_root(legacy, user_id)
    try:
        ensure_directory(root)
        create_marker(root, project_id=DEFAULT_PROJECT_ID, name=DEFAULT_PROJECT_NAME)
        status = "active"
        state = "ok"
    except (ProjectPathError, OSError) as error:
        logger.warning("默认项目初始化失败（%s）：%s", root, error)
        status = "untrusted"
        state = "missing"

    records.insert(
        0,
        {
            "id": DEFAULT_PROJECT_ID,
            "name": DEFAULT_PROJECT_NAME,
            "path": str(root.resolve()),
            "pinned": False,
            "created_at": _now(),
            "updated_at": _now(),
            "last_opened_at": "",
            "marker_state": state,
            "status": status,
        },
    )
    return True


def _migrate_records(raw_projects: Any) -> list[dict[str, Any]]:
    """把旧项目记录转换为新记录，并尽力补齐标记。"""
    records: list[dict[str, Any]] = []
    if not isinstance(raw_projects, list):
        return records
    for item in raw_projects:
        record = _normalize_record(item)
        if record is None:
            continue
        if any(existing["id"] == record["id"] for existing in records):
            record["id"] = uuid.uuid4().hex[:8]
        root = Path(record["path"])
        if root.exists() and root.is_dir():
            state, _detail = marker_state(root, record["id"])
            if state == "missing":
                try:
                    create_marker(root, project_id=record["id"], name=record["name"])
                except ProjectPathError as error:
                    logger.warning("迁移项目标记创建失败 %s：%s", root, error.message)
        records.append(_refresh_record(record))
    return records


def _should_migrate_legacy(cache_key: str) -> bool:
    """只让第一个真实用户继承旧机器级配置，后续用户保持隔离。"""
    if cache_key not in {"anonymous", "console_user"}:
        return False
    users_root = USER_DATA_DIR / "users"
    if not users_root.exists():
        return True
    for child in users_root.iterdir():
        if child.is_dir() and child.name != cache_key:
            if (child / "projects.json").exists():
                return False
    return True


def _load_state(user_id: str) -> dict[str, Any]:
    """惰性加载当前用户的项目登记；首次启动执行旧配置迁移。"""
    cache_key = _safe_user_id(user_id)
    with _lock:
        if cache_key in _states:
            return _states[cache_key]

        path = projects_config_path(cache_key)
        migrated = False
        legacy: dict[str, Any] = {}

        if path.exists():
            data = _read_json(path) or {}
            raw_projects = data.get("projects")
            records = [
                record
                for item in (raw_projects if isinstance(raw_projects, list) else [])
                if (record := _normalize_record(item)) is not None
            ]
        elif _should_migrate_legacy(cache_key) and MACHINE_PROJECTS_PATH.exists():
            # 兼容开发阶段产生的机器级 projects.json。
            data = _read_json(MACHINE_PROJECTS_PATH) or {}
            records = _migrate_records(data.get("projects"))
            legacy = _read_json(LEGACY_CONFIG_PATH) or {}
            migrated = True
            _backup_legacy(MACHINE_PROJECTS_PATH)
        else:
            if _should_migrate_legacy(cache_key):
                legacy = _read_json(LEGACY_CONFIG_PATH) or {}
            records = _migrate_records(legacy.get("projects"))
            migrated = True
            if legacy and LEGACY_CONFIG_PATH.exists():
                _backup_legacy(LEGACY_CONFIG_PATH)

        if _ensure_default(records, legacy, cache_key):
            migrated = True
        for record in records:
            _refresh_record(record)

        payload = {
            "schema_version": PROJECTS_SCHEMA_VERSION,
            "updated_at": _now(),
            "projects": records,
        }
        _states[cache_key] = payload
        if migrated:
            try:
                _write_state(cache_key, records)
            except OSError as error:
                logger.warning("写入用户 projects.json 失败，本次仅用内存状态：%s", error)
        return payload


def _backup_legacy(path: Path) -> None:
    """迁移前备份旧配置，避免不可逆覆盖。"""
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        if not backup.exists():
            shutil.copy2(path, backup)
    except OSError as error:
        logger.warning("备份旧项目配置失败 %s：%s", path, error)


# ============================================================
# 查询
# ============================================================


def reload_project_config(user_id: str | None = None) -> None:
    """丢弃缓存；传 user_id 时只清理该用户。"""
    with _lock:
        if user_id is None:
            _states.clear()
        else:
            _states.pop(_safe_user_id(user_id), None)


def list_projects(user_id: str) -> list[dict[str, Any]]:
    """返回当前用户的全部项目，刷新状态后按置顶、创建时间排序。"""
    with _lock:
        records = [dict(item) for item in _load_state(user_id)["projects"]]
    refreshed = [_refresh_record(record) for record in records]
    refreshed.sort(
        key=lambda item: (
            0 if item.get("pinned") else 1,
            item.get("created_at") or "",
            item.get("id") or "",
        )
    )
    return refreshed


def get_project(user_id: str, project_id: str) -> dict[str, Any] | None:
    """按 ID 读取当前用户的项目并刷新状态。"""
    for record in list_projects(user_id):
        if record["id"] == project_id:
            return record
    return None


def default_project(user_id: str) -> dict[str, Any]:
    """返回当前用户的默认项目；启动阶段已保证其存在。"""
    project = get_project(user_id, DEFAULT_PROJECT_ID)
    if project is None:
        raise ProjectPathError("project_missing", "默认项目不存在")
    return project


def resolve_project(user_id: str, project_id: str | None) -> ProjectContext:
    """解析会话模式：无 project_id 是自由会话，文件落沙箱。"""
    if not project_id:
        return ProjectContext(
            id="free",
            name="自由会话",
            root=None,
            kind="free",
            status="active",
            marker_state="ok",
        )
    project = get_project(user_id, project_id)
    if project is None or project["status"] == "unavailable":
        # 项目已被删除/不可用：本次按自由会话运行，避免整轮对话失败。
        return ProjectContext(
            id="free",
            name="自由会话",
            root=None,
            kind="free",
            status="active",
            marker_state="project_missing",
        )
    return ProjectContext(
        id=project["id"],
        name=project["name"],
        root=Path(project["path"]),
        kind="project",
        status=project["status"],
        marker_state=project["marker_state"],
    )


# ============================================================
# 写入
# ============================================================


def _require_unique_path(records: list[dict[str, Any]], root: Path) -> None:
    target = os.path.normcase(str(root.resolve()))
    for record in records:
        if os.path.normcase(str(Path(record["path"]).resolve())) == target:
            raise ProjectPathError("project_exists", "该目录已登记为项目")


def create_project(
    *,
    user_id: str,
    mode: str,
    name: str,
    path_text: str,
    adopt: bool = False,
) -> dict[str, Any]:
    """新建或附着项目。

    Args:
        user_id: 项目归属用户；登记表按用户隔离。
        mode: ``create`` 创建新目录；``attach`` 选择已有目录。
        name: 项目展示名；attach 时可为空，默认用目录名。
        path_text: 项目目录绝对路径。
        adopt: 允许接管已带标记但未登记的项目目录。
    """
    if mode not in {"create", "attach"}:
        raise ProjectPathError("mode_invalid", "mode 必须是 create 或 attach")

    records = list_projects(user_id)
    root = validate_directory(path_text, allow_create=(mode == "create"))
    display_name = (name or "").strip() or root.name or "未命名项目"

    if mode == "create":
        if root.exists():
            try:
                has_children = any(root.iterdir())
            except OSError as error:
                raise ProjectPathError("path_not_writable", f"无法读取目录：{error}") from error
            if has_children:
                raise ProjectPathError("path_exists", "目录已存在且不为空，请改用选择已有目录")
        _require_unique_path(records, root)
        ensure_directory(root)
        project_id = uuid.uuid4().hex[:8]
        try:
            create_marker(root, project_id=project_id, name=display_name)
        except ProjectPathError:
            try:
                if not any(root.iterdir()):
                    root.rmdir()
            except OSError:
                pass
            raise
        record = _make_record(project_id, display_name, root)
        event = "project.created"
    else:
        root = validate_directory(path_text, allow_create=False)
        _require_unique_path(records, root)
        marker = read_marker(root)
        state, detail = marker_state(root, str((marker or {}).get("project_id") or ""))
        if state == "ok":
            if not adopt:
                raise ProjectPathError("marker_conflict", detail)
            project_id = str(marker["project_id"])
            if any(item["id"] == project_id for item in records):
                raise ProjectPathError("marker_conflict", "该标记已被另一个项目路径登记")
        elif state in {"missing", "invalid", "unsupported"}:
            if state == "unsupported" and not adopt:
                raise ProjectPathError("marker_unsupported", detail)
            project_id = uuid.uuid4().hex[:8]
            create_marker(root, project_id=project_id, name=display_name, overwrite=adopt)
        elif state == "conflict":
            if not adopt:
                raise ProjectPathError("marker_conflict", detail)
            project_id = uuid.uuid4().hex[:8]
            create_marker(root, project_id=project_id, name=display_name, overwrite=True)
        else:  # pragma: no cover - marker_state 不会返回其它值
            raise ProjectPathError("marker_invalid", detail)
        record = _make_record(project_id, display_name, root)
        event = "project.attached"

    records.append(record)
    _write_state(user_id, records)
    append_audit(
        root,
        event,
        {
            "user_id": _safe_user_id(user_id),
            "project_id": record["id"],
            "name": record["name"],
            "path": record["path"],
        },
    )
    return record


def update_project(
    user_id: str,
    project_id: str,
    *,
    name: str | None = None,
    pinned: bool | None = None,
) -> dict[str, Any]:
    """更新项目名称 / 置顶状态。"""
    records = list_projects(user_id)
    record = next((item for item in records if item["id"] == project_id), None)
    if record is None:
        raise ProjectPathError("project_not_found", "项目不存在")
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ProjectPathError("name_empty", "项目名称不能为空")
        record["name"] = cleaned
    if pinned is not None:
        record["pinned"] = bool(pinned)
    record["updated_at"] = _now()
    _write_state(user_id, records)
    return record


def delete_project(user_id: str, project_id: str) -> None:
    """仅移出登记，不删除磁盘内容；默认项目不可删除。"""
    if project_id == DEFAULT_PROJECT_ID:
        raise ProjectPathError("project_protected", "默认项目不可删除")
    records = list_projects(user_id)
    record = next((item for item in records if item["id"] == project_id), None)
    if record is None:
        raise ProjectPathError("project_not_found", "项目不存在")
    append_audit(
        Path(record["path"]),
        "project.unregistered",
        {
            "user_id": _safe_user_id(user_id),
            "project_id": project_id,
            "name": record["name"],
            "path": record["path"],
        },
    )
    _write_state(user_id, [item for item in records if item["id"] != project_id])


def mark_opened(user_id: str, project_id: str) -> None:
    """记录最近打开时间；失败不影响对话。"""
    try:
        records = list_projects(user_id)
        record = next((item for item in records if item["id"] == project_id), None)
        if record is None:
            return
        record["last_opened_at"] = _now()
        _write_state(user_id, records)
    except (OSError, ProjectPathError) as error:
        logger.debug("记录项目最近打开时间失败：%s", error)


def validate_project(
    user_id: str, project_id: str, *, repair: bool = False,
) -> dict[str, Any]:
    """重新校验项目目录与标记；``repair=True`` 时重建标记。"""
    records = list_projects(user_id)
    record = next((item for item in records if item["id"] == project_id), None)
    if record is None:
        raise ProjectPathError("project_not_found", "项目不存在")
    root = Path(record["path"])
    if not root.exists() or not root.is_dir():
        record["marker_state"] = "missing"
        record["status"] = "unavailable"
        _write_state(user_id, records)
        append_audit(
            root,
            "project.validate_failed",
            {"user_id": _safe_user_id(user_id), "project_id": project_id,
             "reason": "directory_unavailable"},
        )
        return record

    if repair:
        try:
            create_marker(
                root,
                project_id=record["id"],
                name=record["name"],
                overwrite=True,
            )
            append_audit(
                root,
                "project.marker_repair",
                {"user_id": _safe_user_id(user_id), "project_id": project_id},
            )
        except ProjectPathError as error:
            logger.warning("项目标记修复失败 %s：%s", root, error.message)
            raise

    state, _detail = marker_state(root, record["id"])
    record["marker_state"] = state
    record["status"] = "active" if state == "ok" else "untrusted"
    record["updated_at"] = _now()
    _write_state(user_id, records)
    if record["status"] != "active":
        append_audit(
            root,
            "project.validate_failed",
            {"user_id": _safe_user_id(user_id), "project_id": project_id,
             "marker_state": state},
        )
    return record


def marker_schema_version() -> int:
    """暴露当前标记版本，供 API 展示/测试使用。"""
    return MARKER_SCHEMA_VERSION
