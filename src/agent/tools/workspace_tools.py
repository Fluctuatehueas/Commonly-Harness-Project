"""项目工作区工具：沙箱产物发布与沙箱内执行。

两个工具共同维护「本机项目」与「沙箱执行域」的边界：

- ``publish_to_project``：把沙箱中生成的成品文件写回当前项目目录；
- ``run_in_sandbox``：把项目内指定文件上传到沙箱，执行命令并返回变更文件提示。

文件类型不做限制：项目是用户真实目录，允许写入代码与脚本；但任何内容都不会
在本机执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from langchain_core.tools import tool

from agent.project_audit import append_audit
from agent.project_context import (
    ProjectContextError,
    current_project,
    current_thread_id,
    sandbox_project_dir,
    sandbox_session_dir,
    require_writable_project,
)
from agent.project_marker import MARKER_DIRNAME

MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
ARTIFACT_NAMESPACE_PREFIX = ("artifacts",)


def _rejected(source: str, reason: str) -> dict[str, str]:
    return {"source": source, "reason": reason}


def _resolve_target(target: str) -> tuple[Path | None, str | None]:
    """把项目相对路径解析为真实路径；不合法时返回中文原因。"""
    text = (target or "").strip()
    if not text:
        return None, "目标路径不能为空"
    if ".." in text or "\x00" in text:
        return None, "目标路径不能包含 .. 或空字符"
    if text.startswith(("/", "\\")) or os.path.isabs(text):
        return None, "目标路径必须是项目内的相对路径，禁止绝对路径"
    if PurePosixPath(text).parts[:1] and PurePosixPath(text).parts[0].lower() == MARKER_DIRNAME:
        return None, ".commonharness 是系统保留目录，禁止写入"
    project = current_project()
    if project.root is None:
        return None, "自由会话没有本机项目目录，文件保留在沙箱"
    root = project.root.resolve()
    try:
        resolved = (root / text).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, "目标路径越出项目目录，已拒绝"
    if resolved.is_dir():
        return None, "目标路径是已存在的目录，无法写入文件"
    return resolved, None


def _read_project_source(source: str) -> tuple[bytes | None, str | None]:
    """从项目目录读取待上传/发布文件。"""
    resolved, error = _resolve_target(source)
    if error or resolved is None:
        return None, error or "源文件路径无效"
    if not resolved.is_file():
        return None, f"项目内不存在文件：{source}"
    try:
        return resolved.read_bytes(), None
    except OSError as error:
        return None, f"读取项目文件失败：{error}"


async def _read_sandbox_source(
    sandbox_backend: Any, source: str,
) -> tuple[bytes | None, str | None]:
    """从沙箱读取文件字节；source 必须是沙箱内绝对路径。"""
    if not source.startswith("/"):
        return None, "source_path 必须是沙箱内绝对路径"
    responses = await asyncio.to_thread(sandbox_backend.download_files, [source])
    response = responses[0] if responses else None
    if response is None or response.error or response.content is None:
        detail = getattr(response, "error", None) or "无响应"
        return None, f"沙箱读取失败：{detail}"
    return response.content, None


async def _record_artifact(
    store: Any,
    *,
    user_id: str,
    project_id: str,
    thread_id: str | None,
    source: str,
    target: str,
    data: bytes,
    overwritten: bool,
    reason: str,
) -> str | None:
    """把发布记录写入 Store；没有 Store 时跳过，不影响文件写入。"""
    if store is None:
        return None
    artifact_id = uuid.uuid4().hex
    payload = {
        "id": artifact_id,
        "thread_id": thread_id,
        "project_id": project_id,
        "source_path": source,
        "target_path": target,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "overwritten": overwritten,
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await store.aput(
            (*ARTIFACT_NAMESPACE_PREFIX, user_id, project_id),
            artifact_id,
            payload,
            index=False,
        )
        return artifact_id
    except Exception:  # noqa: BLE001 - 审计写入失败不应阻断产物落盘
        return None


def create_publish_to_project_tool(
    sandbox_backend: Any | None,
    *,
    store: Any | None = None,
    user_id: str = "anonymous",
) -> Any:
    """构造绑定当前沙箱与当前项目的 ``publish_to_project`` 工具。"""

    @tool
    async def publish_to_project(
        files: Annotated[
            list[dict[str, Any]],
            "待发布文件列表，每项含 source_path（沙箱内绝对路径或项目相对路径）、"
            "可选 target_path（项目内相对路径，缺省用源文件名）与可选 reason",
        ],
    ) -> str:
        """把沙箱产物写回当前项目目录（本机文件域），无需用户下载。

        目标已存在时覆盖写入，结果中以 overwritten=true 标注。
        """
        published: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        total_bytes = 0
        thread_id = current_thread_id() or None

        try:
            project = require_writable_project()
        except ProjectContextError as error:
            return json.dumps(
                {"ok": False, "published": [], "rejected": [_rejected("", str(error))]},
                ensure_ascii=False,
            )

        if not isinstance(files, list) or not files:
            return json.dumps(
                {
                    "ok": False,
                    "published": [],
                    "rejected": [_rejected("", "files 不能为空")],
                },
                ensure_ascii=False,
            )

        for item in files:
            if not isinstance(item, dict):
                rejected.append(_rejected(str(item), "文件项必须是对象"))
                continue
            source = str(item.get("source_path") or "").strip()
            target = str(item.get("target_path") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not source:
                rejected.append(_rejected(source, "source_path 不能为空"))
                continue
            if not target:
                target = Path(source).name
            resolved, error = _resolve_target(target)
            if error or resolved is None:
                rejected.append(_rejected(source, error or "目标路径无效"))
                continue

            if source.startswith("/"):
                if sandbox_backend is None:
                    rejected.append(
                        _rejected(source, "当前未启用沙箱，无法读取沙箱路径")
                    )
                    continue
                data, read_error = await _read_sandbox_source(sandbox_backend, source)
            else:
                data, read_error = _read_project_source(source)
            if read_error or data is None:
                rejected.append(_rejected(source, read_error or "读取文件失败"))
                continue

            size = len(data)
            if size > MAX_SINGLE_FILE_BYTES:
                rejected.append(_rejected(source, "单文件超过 50MB 上限，已拒绝"))
                continue
            if total_bytes + size > MAX_TOTAL_BYTES:
                rejected.append(_rejected(source, "单次发布总量超过 200MB 上限，已拒绝"))
                continue

            overwritten = resolved.exists()
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(resolved.write_bytes, data)
            except OSError as write_error:
                rejected.append(_rejected(source, f"写入项目失败：{write_error}"))
                continue

            total_bytes += size
            artifact_id = await _record_artifact(
                store,
                user_id=user_id,
                project_id=project.id,
                thread_id=thread_id,
                source=source,
                target=target,
                data=data,
                overwritten=overwritten,
                reason=reason,
            )
            append_audit(
                project.root,
                "artifact.published",
                {
                    "user_id": user_id,
                    "project_id": project.id,
                    "thread_id": thread_id,
                    "source": source,
                    "target": target,
                    "size": size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "overwritten": overwritten,
                    "reason": reason,
                    "artifact_id": artifact_id,
                },
            )
            published.append(
                {
                    "source": source,
                    "target": target,
                    "size": size,
                    "overwritten": overwritten,
                    "artifact_id": artifact_id,
                }
            )

        return json.dumps(
            {"ok": not rejected, "published": published, "rejected": rejected},
            ensure_ascii=False,
        )

    return publish_to_project


def _sandbox_entries(sandbox: Any, path: str) -> dict[str, tuple[int | None, str]]:
    """读取沙箱目录快照；兼容 LsResult 与旧式 list[FileInfo]。"""
    result = sandbox.ls(path)
    entries = result.entries if hasattr(result, "entries") else result
    snapshot: dict[str, tuple[int | None, str]] = {}
    for entry in entries or []:
        entry_path = str(entry.get("path") or "")
        if not entry_path:
            continue
        name = PurePosixPath(entry_path).name
        snapshot[name] = (
            entry.get("size"),
            str(entry.get("modified_at") or ""),
        )
    return snapshot


def create_run_in_sandbox_tool(sandbox_backend: Any | None) -> Any:
    """构造 ``run_in_sandbox``：上传项目文件后在沙箱执行命令。"""

    @tool
    async def run_in_sandbox(
        command: Annotated[str, "要在沙箱中执行的命令（shell 语法，cwd 为项目沙箱目录）"],
        files: Annotated[
            list[str],
            "需要上传到沙箱的项目内相对路径列表；不需要项目文件时传空列表",
        ] = [],
        timeout: Annotated[int, "命令超时秒数"] = 120,
    ) -> str:
        """在隔离沙箱执行命令；需要项目文件时先上传，绝不本机执行。"""
        if sandbox_backend is None:
            return json.dumps(
                {
                    "ok": False,
                    "error": "当前未配置沙箱，按安全策略禁止在本机执行任何命令",
                },
                ensure_ascii=False,
            )
        try:
            project = current_project()
        except ProjectContextError as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)

        if project.kind == "free":
            sandbox_dir = sandbox_session_dir(current_thread_id())
            if files:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "自由会话的文件已经在沙箱会话目录中，无需再上传；"
                        "直接执行命令即可",
                    },
                    ensure_ascii=False,
                )
        else:
            sandbox_dir = sandbox_project_dir(project.id)

        uploads: list[tuple[str, bytes]] = []
        rejected: list[dict[str, str]] = []
        for source in files or []:
            data, error = _read_project_source(source)
            if error or data is None:
                rejected.append(_rejected(source, error or "读取文件失败"))
                continue
            relative = PurePosixPath(str(source).strip()).as_posix().lstrip("/")
            uploads.append((f"{sandbox_dir}/{relative}", data))
        if rejected:
            return json.dumps(
                {"ok": False, "error": "部分文件无法上传", "rejected": rejected},
                ensure_ascii=False,
            )

        try:
            # 先建目录，再上传，避免远端 upload 因父目录不存在而失败。
            await asyncio.to_thread(
                sandbox_backend.execute, f"mkdir -p '{sandbox_dir}'", timeout=30
            )
            if uploads:
                responses = await asyncio.to_thread(
                    sandbox_backend.upload_files, uploads
                )
                failed = [
                    {"path": item.path, "error": item.error}
                    for item in responses
                    if item.error
                ]
                if failed:
                    return json.dumps(
                        {"ok": False, "error": "上传到沙箱失败", "rejected": failed},
                        ensure_ascii=False,
                    )

            before = await asyncio.to_thread(_sandbox_entries, sandbox_backend, sandbox_dir)
            response = await asyncio.to_thread(
                sandbox_backend.execute,
                f"cd '{sandbox_dir}' && {command}",
                timeout=max(1, int(timeout)),
            )
            after = await asyncio.to_thread(_sandbox_entries, sandbox_backend, sandbox_dir)

        except Exception as error:  # noqa: BLE001 - 工具边界统一转为可读错误
            return json.dumps(
                {"ok": False, "error": f"沙箱执行失败：{error}"},
                ensure_ascii=False,
            )

        changed = [
            name
            for name, meta in after.items()
            if before.get(name) != meta
        ]
        changed.sort()
        return json.dumps(
            {
                "ok": response.exit_code == 0,
                "exit_code": response.exit_code,
                "output": response.output,
                "truncated": response.truncated,
                "sandbox_dir": sandbox_dir,
                "changed_files": changed,
                "hint": (
                    "如需把沙箱产物落到本机项目，请调用 publish_to_project，"
                    "target_path 使用项目内相对路径。"
                ),
            },
            ensure_ascii=False,
        )

    return run_in_sandbox
