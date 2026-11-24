"""会话文件接口：供右侧文件抽屉查看/下载。

- 自由会话：只列沙箱目录 ``/workspace/sessions/{thread_id}/``
- 项目会话：同时列本机项目目录与沙箱目录 ``/workspace/{project_id}/``；
  文件带 ``origin``（``project`` / ``sandbox``），预览与下载需按来源请求。

项目目录扫描会跳过 ``.git`` / ``node_modules`` / ``.venv`` 等目录，并限制
单次返回数量，避免把大型仓库全部塞进抽屉。
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from agent import project_store
from agent.backends.project_backend import IGNORED_NAMES
from agent.project_context import sandbox_project_dir, sandbox_session_dir
from agent.project_marker import MARKER_DIRNAME
from agent.session_artifacts_store import (
    artifacts_for_thread,
    save_message_artifacts,
)
from api.agent_loader import agent_loader


router = APIRouter()
MAX_FILES = 500
MAX_PREVIEW_BYTES = 10 * 1024 * 1024
FileOrigin = Literal["project", "sandbox"]


async def _session_context(
    user_id: str, thread_id: str,
) -> tuple[str, str | None, Path | None]:
    """返回 (沙箱根目录, project_id, 本机项目根)；会话不存在时抛 404。"""
    session = await agent_loader.get_session(user_id, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    project_id = str(session.get("project_id") or "").strip() or None
    sandbox_root = (
        sandbox_project_dir(project_id) if project_id else sandbox_session_dir(thread_id)
    )
    project_root: Path | None = None
    if project_id:
        record = await asyncio.to_thread(project_store.get_project, user_id, project_id)
        if record is not None:
            try:
                candidate = Path(str(record.get("path") or "")).expanduser()
                if candidate.exists() and candidate.is_dir():
                    project_root = candidate.resolve()
            except (OSError, ValueError):
                project_root = None
    return sandbox_root, project_id, project_root


async def _sandbox(user_id: str) -> Any:
    try:
        return await agent_loader.get_sandbox_backend(user_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 沙箱不可用时统一返回 503
        raise HTTPException(status_code=503, detail=f"沙箱不可用: {exc}") from exc


def _relative(root: str, absolute: str) -> str:
    if absolute == root:
        return "/"
    prefix = f"{root}/"
    if absolute.startswith(prefix):
        return absolute[len(root):]
    return absolute


def _sandbox_sizes(sandbox: Any, paths: list[str]) -> dict[str, int]:
    """批量读取沙箱文件大小。

    deepagents 的 ``sandbox.glob`` 只返回 path/is_dir，size 恒为 0；这里用一次
    沙箱内 stat 补齐，而不是给每个文件单独发一次请求。
    """
    if not paths:
        return {}
    payload = base64.b64encode(json.dumps(paths).encode("utf-8")).decode("ascii")
    script = (
        'python3 -c "'
        "import base64,json,os\n"
        f"paths=json.loads(base64.b64decode('{payload}').decode('utf-8'))\n"
        "for p in paths:\n"
        "    try:\n"
        "        st=os.stat(p)\n"
        "        print(json.dumps({'path':p,'size':st.st_size}))\n"
        "    except OSError:\n"
        "        pass\n"
        '"'
    )
    try:
        result = sandbox.execute(script)
    except Exception:  # noqa: BLE001 - 取不到大小时退化为 0，不影响文件列表
        return {}
    sizes: dict[str, int] = {}
    for line in str(getattr(result, "output", "") or "").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        path = data.get("path")
        if isinstance(path, str):
            try:
                sizes[path] = int(data.get("size") or 0)
            except (TypeError, ValueError):
                continue
    return sizes


def _list_sync(sandbox: Any, root: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        result = sandbox.glob("*", root)
        matches = result.matches or []
    except Exception:  # noqa: BLE001 - 目录不存在时按空列表处理
        return entries
    seen: set[str] = set()
    absolute_paths: list[str] = []
    for item in matches:
        absolute = str(item.get("path") or "")
        if not absolute or absolute in seen:
            continue
        seen.add(absolute)
        absolute_paths.append(absolute)
    # 先按路径排序再截断，保证返回的一定是展示出来的那批文件；
    # 只对这批文件取 size，避免给上万条匹配发一条超长命令。
    absolute_paths.sort()
    absolute_paths = absolute_paths[:MAX_FILES]
    sizes = _sandbox_sizes(sandbox, absolute_paths)
    for absolute in absolute_paths:
        entries.append(
            {
                "path": _relative(root, absolute),
                "size": sizes.get(absolute, 0),
                "modified_at": None,
                "origin": "sandbox",
            }
        )
    return entries


def _list_project_sync(root: Path) -> list[dict[str, Any]]:
    """扫描本机项目目录，返回相对路径文件清单（跳过忽略目录）。"""
    entries: list[dict[str, Any]] = []
    try:
        walker = os.walk(root)
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                name for name in dirnames if name.lower() not in IGNORED_NAMES
            ]
            for name in filenames:
                file_path = Path(dirpath) / name
                try:
                    stat = file_path.stat()
                    relative = file_path.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                entries.append(
                    {
                        "path": f"/{relative}",
                        "size": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "origin": "project",
                    }
                )
                if len(entries) >= MAX_FILES:
                    return entries
    except OSError:
        return entries
    return entries


def _resolve_path(root: str, path: str) -> str:
    text = (path or "").strip()
    if not text or ".." in text or "\x00" in text:
        raise HTTPException(status_code=422, detail="path 必须非空且不含 ..")
    relative = PurePosixPath(text.lstrip("/"))
    if not relative.parts:
        raise HTTPException(status_code=422, detail="path 不能为空")
    absolute = f"{root}/{relative.as_posix()}"
    try:
        resolved = PurePosixPath(absolute)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="path 无效") from exc
    if not str(resolved).startswith(f"{root}/"):
        raise HTTPException(status_code=422, detail="path 越出会话目录")
    return str(resolved)


def _resolve_project_path(root: Path, path: str) -> Path:
    """把项目内相对路径解析为真实文件；拒绝越界与保留目录。"""
    text = (path or "").strip()
    if not text or ".." in text or "\x00" in text:
        raise HTTPException(status_code=422, detail="path 必须非空且不含 ..")
    relative = PurePosixPath(text.lstrip("/"))
    if not relative.parts:
        raise HTTPException(status_code=422, detail="path 不能为空")
    if relative.parts[0].lower() == MARKER_DIRNAME:
        raise HTTPException(status_code=422, detail=".commonharness 是系统保留目录")
    try:
        target = (root / relative.as_posix()).resolve()
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="path 越出项目目录") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


@router.get("/threads/{thread_id}/files")
async def list_session_files(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """列出会话文件：项目会话合并本机项目目录与沙箱目录。"""
    sandbox_root, project_id, project_root = await _session_context(user_id, thread_id)
    sandbox_files: list[dict[str, Any]] = []
    if project_root is None:
        # 自由会话只有沙箱一个文件域；沙箱不可用时保持原有 503 语义。
        sandbox = await _sandbox(user_id)
        sandbox_files = await asyncio.to_thread(_list_sync, sandbox, sandbox_root)
    else:
        # 项目会话即使沙箱不可用，也应能看到本机项目文件。
        try:
            sandbox = await _sandbox(user_id)
        except HTTPException:
            sandbox = None
        if sandbox is not None:
            sandbox_files = await asyncio.to_thread(_list_sync, sandbox, sandbox_root)
    project_files: list[dict[str, Any]] = []
    if project_root is not None:
        project_files = await asyncio.to_thread(_list_project_sync, project_root)
        project_files.sort(key=lambda item: item["path"])
    return {
        "source": "project+sandbox" if project_root is not None else "sandbox",
        "workspace": str(project_root) if project_root is not None else sandbox_root,
        "sandbox_root": sandbox_root,
        "project_id": project_id,
        "files": project_files + sandbox_files,
    }


async def _read_content(
    *,
    source: FileOrigin,
    path: str,
    sandbox: Any,
    sandbox_root: str,
    project_root: Path | None,
) -> bytes:
    """按来源读取文件字节；读取失败转换为 HTTP 错误。"""
    if source == "project":
        if project_root is None:
            raise HTTPException(status_code=404, detail="当前会话没有可用的本机项目目录")
        target = _resolve_project_path(project_root, path)
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise HTTPException(status_code=404, detail=f"读取文件失败：{exc}") from exc
        if size > MAX_PREVIEW_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 10MB，请下载后查看")
        try:
            return await asyncio.to_thread(target.read_bytes)
        except OSError as exc:
            raise HTTPException(status_code=404, detail=f"读取文件失败：{exc}") from exc

    absolute = _resolve_path(sandbox_root, path)
    responses = await asyncio.to_thread(sandbox.download_files, [absolute])
    response = responses[0] if responses else None
    if response is None or response.error or response.content is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return response.content


@router.get("/threads/{thread_id}/files/content")
async def read_session_file(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
    source: FileOrigin = Query("sandbox"),
) -> dict[str, Any]:
    """读取单个文件的文本内容；二进制返回 415。"""
    sandbox_root, _project_id, project_root = await _session_context(user_id, thread_id)
    sandbox = await _sandbox(user_id) if source == "sandbox" else None
    content = await _read_content(
        source=source,
        path=path,
        sandbox=sandbox,
        sandbox_root=sandbox_root,
        project_root=project_root,
    )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="文件为二进制，无法预览") from exc
    if "\x00" in text:
        raise HTTPException(status_code=415, detail="文件为二进制，无法预览")
    return {"path": path, "content": text, "encoding": "utf-8", "source": source}


@router.get("/threads/{thread_id}/files/download")
async def download_session_file(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
    source: FileOrigin = Query("sandbox"),
    inline: bool = Query(False, description="图片消息内联展示时使用正确的 media type"),
) -> Response:
    """按原始字节下载文件；图片可在 Markdown 气泡内联渲染。"""
    sandbox_root, _project_id, project_root = await _session_context(user_id, thread_id)
    sandbox = await _sandbox(user_id) if source == "sandbox" else None
    content = await _read_content(
        source=source,
        path=path,
        sandbox=sandbox,
        sandbox_root=sandbox_root,
        project_root=project_root,
    )
    filename = PurePosixPath(path).name or "download"
    # Windows 的 mimetypes 会读注册表，.pdf 可能被映射成 octet-stream，这里显式兜底。
    if filename.lower().endswith(".pdf"):
        media_type = "application/pdf"
    else:
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # 只有图片和 PDF 允许内联展示，其他文件仍强制下载，避免把 HTML/SVG 当页面执行。
    allow_inline = inline and (
        media_type.startswith("image/") or media_type == "application/pdf"
    )
    disposition = (
        f"inline; filename*=UTF-8''{quote(filename)}"
        if allow_inline
        else f"attachment; filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=content,
        media_type=media_type if allow_inline else "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


class SaveArtifactsRequest(BaseModel):
    """某条助手消息的产出文件清单。"""

    message_id: str = Field(min_length=1, max_length=200)
    files: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/threads/{thread_id}/artifacts")
async def list_session_artifacts(
    thread_id: str,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """返回会话内「助手消息 ID -> 本轮产出文件」的映射，供刷新后回显。"""
    await agent_loader.require_session(user_id, thread_id, allow_archived=True)
    store = await agent_loader.get_store()
    return {
        "artifacts": await artifacts_for_thread(
            store, user_id=user_id, thread_id=thread_id
        )
    }


@router.put("/threads/{thread_id}/artifacts")
async def save_session_artifacts(
    thread_id: str,
    body: SaveArtifactsRequest,
    user_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """前端每轮结束后写入本轮产出，刷新时再按消息 ID 取回。"""
    await agent_loader.require_session(user_id, thread_id, allow_archived=True)
    store = await agent_loader.get_store()
    files = await save_message_artifacts(
        store,
        user_id=user_id,
        thread_id=thread_id,
        message_id=body.message_id,
        files=body.files,
    )
    return {"message_id": body.message_id, "files": files}
