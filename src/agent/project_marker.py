"""项目目录标记（``.commonharness/project.json``）的创建、校验与修复。

标记是「此目录由本应用托管」的信任信号：只有带标记的项目目录才允许 Agent
写入。标记不是安全凭证，只用于防止误把系统目录当作项目。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.config import USER_DATA_DIR
from utils.integrity import integrity_hint

MARKER_DIRNAME = ".commonharness"
MARKER_FILENAME = "project.json"
MARKER_SCHEMA_VERSION = 1

MarkerState = Literal["ok", "missing", "invalid", "unsupported", "conflict"]
logger = logging.getLogger(__name__)


class ProjectPathError(ValueError):
    """目录或标记校验失败；``code`` 供 API 返回结构化错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def marker_dir(root: Path) -> Path:
    """返回项目根下的标记目录路径。"""
    return root / MARKER_DIRNAME


def marker_file(root: Path) -> Path:
    """返回项目根下的标记文件路径。"""
    return marker_dir(root) / MARKER_FILENAME


def _norm(path: Path) -> str:
    """Windows 路径大小写不敏感；统一用 normcase 比较。"""
    return os.path.normcase(str(path))


def _protected_dirs() -> list[Path]:
    """需要拒绝的系统目录；不存在或无法解析的项直接跳过。"""
    candidates: list[Path] = []
    for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value))
    candidates.append(USER_DATA_DIR)
    return candidates


def is_forbidden_root(root: Path) -> bool:
    """判断目录是否属于禁止登记的范围（盘符根、系统目录、数据区根）。"""
    resolved = root.resolve()
    # 盘符根 / 挂载根：resolve 后只剩 anchor。
    if resolved == Path(resolved.anchor):
        return True
    for protected in _protected_dirs():
        try:
            protected_resolved = protected.resolve()
        except (OSError, ValueError):
            continue
        if _norm(resolved) == _norm(protected_resolved):
            return True
        try:
            if resolved.is_relative_to(protected_resolved):
                return True
        except ValueError:
            continue
    return False


def validate_directory(
    path_text: str, *, allow_create: bool = False,
) -> Path:
    """校验目录可用性；失败时抛 ``ProjectPathError``。

    Args:
        path_text: 用户输入的路径。
        allow_create: 为 True 时允许目录不存在（由调用方创建）。
    """
    text = (path_text or "").strip()
    if not text:
        raise ProjectPathError("path_empty", "路径不能为空")
    try:
        candidate = Path(text).expanduser()
    except (OSError, ValueError) as error:
        raise ProjectPathError("path_invalid", "路径无效") from error
    if not candidate.is_absolute():
        raise ProjectPathError("path_not_absolute", "路径必须是绝对路径")
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError) as error:
        raise ProjectPathError("path_invalid", "路径无效") from error

    if resolved.name.lower() == MARKER_DIRNAME:
        raise ProjectPathError("path_forbidden", "不能把标记目录本身作为项目")
    if is_forbidden_root(resolved):
        raise ProjectPathError("path_forbidden", f"不允许使用该目录：{resolved}")

    if not resolved.exists():
        if not allow_create:
            raise ProjectPathError("path_not_found", f"目录不存在：{resolved}")
        return resolved
    if not resolved.is_dir():
        raise ProjectPathError("path_not_dir", f"路径不是目录：{resolved}")
    # 不在注册阶段做写探针：某些目录（含受控文件夹/沙箱运行环境）会误判。
    # 真正的写权限由创建 .commonharness 标记和后续文件写入来验证。
    return resolved


def read_marker(root: Path) -> dict[str, Any] | None:
    """读取标记文件；缺失或损坏时返回 None。"""
    path = marker_file(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def marker_state(root: Path, expected_project_id: str) -> tuple[MarkerState, str]:
    """返回标记状态与中文说明，供 API 和运行时降级判断。"""
    directory = marker_dir(root)
    if not directory.exists():
        return "missing", "缺少 .commonharness 标记目录"
    if directory.is_symlink() or not directory.is_dir():
        return "invalid", ".commonharness 必须是真实目录，不能是符号链接"

    data = read_marker(root)
    if data is None:
        return "invalid", "project.json 缺失或无法解析"

    version = data.get("schema_version")
    if not isinstance(version, int) or version != MARKER_SCHEMA_VERSION:
        return "unsupported", f"不支持的标记版本：{version!r}"
    recorded_id = str(data.get("project_id") or "").strip()
    if not recorded_id:
        return "invalid", "project.json 缺少 project_id"
    if recorded_id != expected_project_id:
        return "conflict", "该目录已被另一个项目登记"

    recorded_root = str((data.get("host") or {}).get("root") or "").strip()
    if recorded_root:
        try:
            recorded_path = Path(recorded_root).resolve()
        except (OSError, ValueError):
            recorded_path = Path(recorded_root)
        if _norm(recorded_path) != _norm(root.resolve()):
            return "invalid", "目录已被移动或复制，标记与当前路径不一致"
    return "ok", "标记正常"


def create_marker(
    root: Path, *, project_id: str, name: str, overwrite: bool = False,
) -> None:
    """创建或修复标记文件。

    Args:
        root: 项目根目录（必须已存在且可写）。
        project_id: 项目稳定 ID。
        name: 项目展示名。
        overwrite: 为 True 时允许覆盖损坏或冲突的标记（修复入口使用）。
    """
    directory = marker_dir(root)
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise ProjectPathError(
                "marker_invalid", ".commonharness 必须是真实目录，不能是符号链接"
            )
        state, detail = marker_state(root, project_id)
        if state == "ok" and not overwrite:
            return
        if state == "conflict" and not overwrite:
            raise ProjectPathError("marker_conflict", detail)
        if state == "unsupported" and not overwrite:
            raise ProjectPathError("marker_unsupported", detail)
    else:
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise ProjectPathError(
                "marker_create_failed",
                f"无法创建标记目录：{error}。"
                f"{integrity_hint()}"
                "若后端运行在文件系统沙箱或受控目录中，请从普通终端启动服务，"
                "或选择有写权限的目录。",
            ) from error

    payload = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "project_id": project_id,
        "name": name,
        "created_by": "CommonHarness",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "platform": "windows" if os.name == "nt" else os.name,
            "root": str(root.resolve()),
        },
        "policy": {
            "agent_write": True,
            "agent_execute": False,
            "agent_deny_paths": [MARKER_DIRNAME],
        },
    }
    target = marker_file(root)
    temp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, target)
    except OSError as error:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProjectPathError(
            "marker_create_failed",
            f"无法写入标记文件：{error}。"
            f"{integrity_hint()}"
            "若后端运行在文件系统沙箱或受控目录中，请从普通终端启动服务，"
            "或选择有写权限的目录。",
        ) from error


def remove_marker(root: Path) -> None:
    """删除标记文件；仅用于测试或显式清理，不删除项目目录。"""
    for path in (marker_file(root), marker_dir(root)):
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir() and not path.is_symlink():
                # 标记目录只应包含 project.json；清空后再删目录。
                for child in path.iterdir():
                    if child.is_file():
                        child.unlink()
                path.rmdir()
        except OSError:
            logger.warning("清理标记失败：%s", path)


def default_project_root() -> Path:
    """系统默认项目目录；数据目录不可写时由 store 做降级处理。"""
    return (USER_DATA_DIR / "workspaces" / "default").resolve()


def ensure_directory(path: Path) -> None:
    """创建项目目录；失败时抛出结构化错误。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ProjectPathError(
            "path_not_writable",
            f"无法创建目录：{error}。"
            f"{integrity_hint()}"
            "若后端运行在文件系统沙箱或受控目录中，请从普通终端启动服务，"
            "或选择有写权限的目录。",
        ) from error
