"""通用文件索引：内存缓存 + 可删磁盘快照，不落 PostgreSQL。

索引只保存元信息（名称、路径、mtime、size 等），正文永远从文件读取。
缓存损坏或签名不匹配时回退实时扫描，不影响主流程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MEMORY: dict[str, tuple[str, list[dict[str, Any]]]] = {}


def _config() -> Any:
    from agent import config

    return config


def _safe_user(user_id: str | None) -> str:
    from agent.identity import safe_user_id

    return safe_user_id(user_id)


def index_path(module: str, user_id: str | None) -> Path:
    root = Path(_config().HARNESS_HOME) / "cache" / "index"
    return root / f"{module}-{_safe_user(user_id)}.json"


def _file_stats(root: Path) -> tuple[int, int, int]:
    latest = 0
    total = 0
    count = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            latest = max(latest, stat.st_mtime_ns)
            total += stat.st_size
            count += 1
    except OSError:
        pass
    return latest, total, count


def signature(roots: list[Path]) -> str:
    """由根目录/文件的 mtime、大小、数量生成签名。"""
    parts: list[str] = []
    for root in roots:
        try:
            if root.is_file():
                stat = root.stat()
                parts.append(f"{root}|f|{stat.st_mtime_ns}|{stat.st_size}")
            elif root.is_dir():
                latest, total, count = _file_stats(root)
                parts.append(f"{root}|d|{latest}|{total}|{count}")
            else:
                parts.append(f"{root}|missing")
        except OSError:
            parts.append(f"{root}|error")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig") or "null")
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
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
    except OSError:
        logger.debug("写入索引快照失败：%s", path, exc_info=True)


def get(
    module: str,
    user_id: str | None,
    roots: list[Path],
    builder: Callable[[], list[dict[str, Any]]],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """读取索引；签名不匹配时调用 builder 重建。"""
    key = f"{module}:{_safe_user(user_id)}"
    current = signature(roots)
    if not force:
        cached = _MEMORY.get(key)
        if cached and cached[0] == current:
            return [dict(item) for item in cached[1]]

        snapshot = _read_snapshot(index_path(module, user_id))
        if (
            snapshot.get("signature") == current
            and isinstance(snapshot.get("entries"), list)
        ):
            entries = [
                dict(item) for item in snapshot["entries"] if isinstance(item, dict)
            ]
            _MEMORY[key] = (current, entries)
            return [dict(item) for item in entries]

    entries = builder()
    _MEMORY[key] = (current, entries)
    _write_snapshot(
        index_path(module, user_id),
        {"signature": current, "entries": entries},
    )
    return [dict(item) for item in entries]


def invalidate(module: str, user_id: str | None = None) -> None:
    """安装/卸载/启停后调用；不传 user_id 时清理整个模块。"""
    if user_id is not None:
        key = f"{module}:{_safe_user(user_id)}"
        _MEMORY.pop(key, None)
        try:
            index_path(module, user_id).unlink(missing_ok=True)
        except OSError:
            pass
        return
    prefix = f"{module}:"
    for key in [name for name in _MEMORY if name.startswith(prefix)]:
        _MEMORY.pop(key, None)
