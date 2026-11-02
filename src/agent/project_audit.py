"""项目审计日志：写入项目内 ``.commonharness/audit.jsonl``。

只记录低频、有长期价值的事件（项目生命周期、产物发布、校验失败），
不记录每次文件读写；审计失败不影响主流程。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.project_marker import MARKER_DIRNAME

AUDIT_FILENAME = "audit.jsonl"
_lock = threading.Lock()
logger = logging.getLogger(__name__)


def audit_path(root: Path) -> Path:
    """返回项目审计文件路径。"""
    return root / MARKER_DIRNAME / AUDIT_FILENAME


def append_audit(root: Path, event: str, payload: dict[str, Any] | None = None) -> None:
    """追加一条 JSONL 审计记录；任何失败都只记录告警。"""
    # 目录不存在时不能创建审计目录，否则会在错误路径上留下半成品。
    try:
        if not root.exists() or not root.is_dir():
            return
    except OSError:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **(payload or {}),
    }
    path = audit_path(root)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
    except OSError as error:
        logger.warning("写入项目审计日志失败 %s（%s）", path, error)
