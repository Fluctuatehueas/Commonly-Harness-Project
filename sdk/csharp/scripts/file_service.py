"""线程工作区文件 gRPC 服务。

gRPC ChatService 当前使用无沙箱模式，文件存储在 LangGraph 线程状态的
``files`` 通道；与 HTTP /threads/{id}/files 的 state 分支语义一致。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(_BASE / "model"), str(_BASE / "grpc_service")]

import grpc  # noqa: E402

import File_pb2  # noqa: E402
import File_pb2_grpc  # noqa: E402

from api.agent_loader import AgentLoader  # noqa: E402


def _user_id(request: Any) -> str:
    return request.user_id.strip() if request.user_id and request.user_id.strip() else "anonymous"


def _validate_path(path: str) -> None:
    if not path or not path.startswith("/") or ".." in path:
        raise ValueError("path 必须非空、以 / 开头且不含 ..")


def _content_to_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(line) for line in content)
    return str(content or "")


def _content_size(content: Any) -> int:
    if isinstance(content, list):
        return len("".join(str(line) for line in content).encode("utf-8"))
    return len(str(content or "").encode("utf-8"))


class FileServiceServicer(File_pb2_grpc.FileServiceServicer):
    """会话工作区文件读写。"""

    def __init__(self, loader: AgentLoader) -> None:
        self._loader = loader

    async def _get_agent_and_config(self, user_id: str, thread_id: str):
        if await self._loader.get_session(user_id, thread_id) is None:
            return None, None
        agent = await self._loader.get_agent_for_user(
            user_id=user_id,
            username=user_id,
            thread_id=thread_id,
        )
        config = self._loader.create_config(
            thread_id=thread_id,
            user_id=user_id,
            username=user_id,
        )
        return agent, config

    async def _get_files(self, user_id: str, thread_id: str) -> dict[str, Any] | None:
        agent, config = await self._get_agent_and_config(user_id, thread_id)
        if agent is None or config is None:
            return None
        state = await agent.aget_state(config)
        return dict(state.values.get("files") or {})

    async def ListThreadFiles(
        self,
        request: File_pb2.ListThreadFilesRequest,
        context: grpc.aio.ServicerContext,
    ) -> File_pb2.ListThreadFilesResponse:
        user_id = _user_id(request)
        files = await self._get_files(user_id, request.thread_id)
        if files is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        entries = []
        for path, meta in files.items():
            content = meta.get("content") if isinstance(meta, dict) else meta
            modified_at = meta.get("modified_at") if isinstance(meta, dict) else None
            entries.append(
                File_pb2.FileEntry(
                    path=str(path),
                    size=_content_size(content),
                    modified_at=str(modified_at) if modified_at else "",
                )
            )
        entries.sort(key=lambda item: item.path)
        return File_pb2.ListThreadFilesResponse(source="state", files=entries)

    async def ReadThreadFile(
        self,
        request: File_pb2.ReadThreadFileRequest,
        context: grpc.aio.ServicerContext,
    ) -> File_pb2.ReadThreadFileResponse:
        user_id = _user_id(request)
        try:
            _validate_path(request.path)
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        files = await self._get_files(user_id, request.thread_id)
        if files is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        meta = files.get(request.path)
        if meta is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "文件不存在",
            )

        content = meta.get("content") if isinstance(meta, dict) else meta
        text = _content_to_text(content)
        if "\x00" in text:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "文件为二进制，无法预览",
            )

        return File_pb2.ReadThreadFileResponse(
            path=request.path,
            content=text,
            encoding="utf-8",
        )

    async def WriteThreadFile(
        self,
        request: File_pb2.WriteThreadFileRequest,
        context: grpc.aio.ServicerContext,
    ) -> File_pb2.WriteThreadFileResponse:
        user_id = _user_id(request)
        try:
            _validate_path(request.path)
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        agent, config = await self._get_agent_and_config(
            user_id,
            request.thread_id,
        )
        if agent is None or config is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "会话不存在或无权访问",
            )

        state = await agent.aget_state(config)
        files = dict(state.values.get("files") or {})
        existing = files.get(request.path)
        now = datetime.now(timezone.utc).isoformat()
        created_at = (
            str(existing.get("created_at"))
            if isinstance(existing, dict) and existing.get("created_at")
            else now
        )

        # files reducer 为合并语义，只传变更的 path。
        await agent.aupdate_state(
            config,
            {
                "files": {
                    request.path: {
                        "content": request.content.split("\n"),
                        "created_at": created_at,
                        "modified_at": now,
                    }
                }
            },
        )
        return File_pb2.WriteThreadFileResponse(success=True)
