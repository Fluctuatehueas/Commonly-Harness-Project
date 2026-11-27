"""v1.1 存储结构迁移：旧目录 -> HARNESS_HOME 布局。

原则：只复制、不删除。迁移失败不阻断启动，旧数据仍然可用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_VERSION = "1.1"
MARKER_NAME = ".storage-migration.json"

_MIGRATED_USERS: set[str] = set()


def _config() -> Any:
    from agent import config

    return config


def _safe_user(user_id: str | None) -> str:
    from agent.identity import safe_user_id

    return safe_user_id(user_id)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig") or "null")
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
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


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hashes(root: Path) -> dict[str, str] | None:
    if not root.is_dir():
        return None
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        try:
            hashes[path.relative_to(root).as_posix()] = _file_hash(path)
        except OSError:
            return None
    return hashes


def _same_tree(left: Path, right: Path) -> bool:
    """目录内容完全一致（用于判断是否是未改动的内置技能）。"""
    left_hashes = _tree_hashes(left)
    right_hashes = _tree_hashes(right)
    return left_hashes is not None and left_hashes == right_hashes


def _copy_tree(source: Path, target: Path) -> bool:
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return True


def _skill_targets(skills_root: Path) -> list[tuple[str, Path, Path]]:
    """返回 (target, 相对目录, 绝对目录)，target 为 main 或 subagents/<agent>。"""
    result: list[tuple[str, Path, Path]] = []
    main_dir = skills_root / "main"
    if main_dir.is_dir():
        for item in main_dir.iterdir():
            if item.is_dir() and (item / "SKILL.md").is_file():
                result.append(("main", Path("main") / item.name, item))
    subagents_dir = skills_root / "subagents"
    if subagents_dir.is_dir():
        for agent_dir in subagents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            for item in agent_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").is_file():
                    result.append(
                        (
                            f"subagents/{agent_dir.name}",
                            Path("subagents") / agent_dir.name / item.name,
                            item,
                        )
                    )
    result.sort(key=lambda entry: entry[1].as_posix())
    return result


def _migrate_skills(
    legacy_root: Path,
    target_root: Path,
    builtin_root: Path,
    disabled_legacy: Path | None = None,
) -> None:
    if not legacy_root.is_dir():
        return
    target_root.mkdir(parents=True, exist_ok=True)
    state_path = target_root / "skills-state.json"
    state = _read_json(state_path)
    disabled_builtin = {
        str(item) for item in (state.get("disabled_builtin") or []) if item
    }

    for target, relative, source in _skill_targets(legacy_root):
        builtin_dir = builtin_root / relative
        if target.startswith("subagents/"):
            builtin_dir = builtin_root / relative
        if _same_tree(source, builtin_dir):
            # 未改动的内置技能交给 overlay，不复制。
            continue
        _copy_tree(source, target_root / relative)

    if disabled_legacy and disabled_legacy.is_dir():
        for target, relative, source in _skill_targets(disabled_legacy):
            builtin_dir = builtin_root / relative
            if _same_tree(source, builtin_dir):
                disabled_builtin.add(f"{target}:{relative.name}")
                continue
            _copy_tree(source, target_root / "skills-disabled" / relative)

    payload = {
        "builtin_version": str(state.get("builtin_version") or MIGRATION_VERSION),
        "disabled_builtin": sorted(disabled_builtin),
    }
    _write_json(state_path, payload)


def _migrate_user_locked(user_id: str) -> None:
    config = _config()
    home = Path(config.HARNESS_HOME)
    legacy_user = Path(config.USER_DATA_DIR) / "users" / user_id
    builtin_root = Path(config.SKILLS_ROOT)

    legacy_mcp = legacy_user / "mcp" / "servers.json"
    new_mcp = home / "mcp" / "users" / user_id / "servers.json"
    if not new_mcp.exists() and legacy_mcp.is_file():
        new_mcp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_mcp, new_mcp)
        logger.info("迁移 MCP 配置：%s -> %s", legacy_mcp, new_mcp)

    legacy_skills = legacy_user / "skills"
    target_skills = home / "skills" / "users" / user_id
    if legacy_skills.is_dir():
        _migrate_skills(
            legacy_skills,
            target_skills,
            builtin_root,
            legacy_user / "skills-disabled",
        )
        logger.info("迁移用户技能：%s -> %s", legacy_skills, target_skills)
    else:
        # 打包旧版本的全局技能库：单用户场景下作为用户技能迁移来源。
        global_legacy = Path(config.USER_DATA_DIR) / "skills"
        if global_legacy.is_dir() and not target_skills.exists():
            _migrate_skills(global_legacy, target_skills, builtin_root)
            logger.info("迁移旧全局技能库：%s -> %s", global_legacy, target_skills)


def migrate_user(user_id: str | None) -> None:
    """迁移单个用户；幂等，失败只告警。"""
    uid = _safe_user(user_id)
    config = _config()
    key = f"{config.HARNESS_HOME}:{uid}"
    if key in _MIGRATED_USERS:
        return
    _MIGRATED_USERS.add(key)
    try:
        _migrate_user_locked(uid)
    except Exception:  # noqa: BLE001 - 迁移失败不能阻断主流程
        _MIGRATED_USERS.discard(key)
        logger.warning("用户存储迁移失败：%s", uid, exc_info=True)


def migrate_legacy_runtime() -> None:
    """把旧 ``.mcp`` 运行态目录的日志迁到新位置；npm 缓存不迁，自动重建。"""
    config = _config()
    target_root = Path(config.MCP_ROOT) / "users"
    candidates = (
        Path(config.PROJECT_ROOT) / ".mcp" / "users",
        Path(config.USER_DATA_DIR) / ".mcp" / "users",
    )
    for legacy_root in candidates:
        if not legacy_root.is_dir():
            continue
        for user_dir in legacy_root.iterdir():
            if not user_dir.is_dir():
                continue
            for server_dir in user_dir.iterdir():
                if not server_dir.is_dir():
                    continue
                target = target_root / user_dir.name / "runtime" / server_dir.name
                if target.exists():
                    continue
                legacy_logs = server_dir / "logs"
                if legacy_logs.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(legacy_logs, target / "logs", dirs_exist_ok=True)
                    logger.info("迁移 MCP 日志：%s -> %s", legacy_logs, target / "logs")


def migrate_all() -> None:
    """启动时执行一次：所有用户 + 旧 MCP 运行态。"""
    config = _config()
    home = Path(config.HARNESS_HOME)
    marker = home / MARKER_NAME
    previous = _read_json(marker)
    if previous.get("version") == MIGRATION_VERSION:
        return
    users_root = Path(config.USER_DATA_DIR) / "users"
    if users_root.is_dir():
        for directory in users_root.iterdir():
            if directory.is_dir():
                migrate_user(directory.name)
    migrate_legacy_runtime()
    _write_json(
        marker,
        {
            "version": MIGRATION_VERSION,
            "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    logger.info("存储结构迁移完成：%s", home)
