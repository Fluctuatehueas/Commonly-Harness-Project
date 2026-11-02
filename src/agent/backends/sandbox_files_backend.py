"""自由会话的沙箱文件后端。

把 Agent 的虚拟路径（``/notes.md``）映射到该会话在沙箱内的独立目录
``/workspace/sessions/{thread_id}/``，使多个自由会话互不串文件；项目会话
不使用本后端。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    GrepMatch,
    FileInfo,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent.project_context import current_thread_id, sandbox_session_dir


class SandboxSessionFilesBackend(BackendProtocol):
    """自由会话文件域：所有文件操作都落在沙箱会话目录。"""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox
        self._created_roots: set[str] = set()
        self._lock = threading.RLock()

    def _root(self) -> str:
        return sandbox_session_dir(current_thread_id())

    def _abs(self, key: str) -> str:
        text = str(key or "/").strip() or "/"
        if ".." in PurePosixPath(text).parts:
            raise ValueError("路径不允许包含 ..")
        virtual = text if text.startswith("/") else f"/{text}"
        root = self._root()
        return root if virtual == "/" else f"{root}{virtual}"

    def _virtual(self, absolute: str) -> str:
        root = self._root()
        if absolute == root:
            return "/"
        if absolute.startswith(f"{root}/"):
            return absolute[len(root) :]
        return absolute

    def _ensure_root(self) -> None:
        root = self._root()
        with self._lock:
            if root in self._created_roots:
                return
            self._sandbox.execute(f"mkdir -p '{root}'", timeout=30)
            self._created_roots.add(root)

    # ---- 文件操作 ----

    def ls(self, path: str) -> LsResult:
        try:
            self._ensure_root()
            result = self._sandbox.ls(self._abs(path))
        except ValueError as error:
            return LsResult(error=str(error))
        entries = [
            {**entry, "path": self._virtual(str(entry.get("path", "")))}
            for entry in (result.entries or [])
        ]
        return LsResult(error=result.error, entries=entries)

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            return self._sandbox.read(self._abs(file_path), offset=offset, limit=limit)
        except ValueError as error:
            return ReadResult(error=str(error))

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000,
    ) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self._ensure_root()
            result = self._sandbox.write(self._abs(file_path), content)
        except ValueError as error:
            return WriteResult(error=str(error))
        if result.path:
            return WriteResult(path=self._virtual(result.path))
        return result

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            result = self._sandbox.edit(
                self._abs(file_path), old_string, new_string, replace_all
            )
        except ValueError as error:
            return EditResult(error=str(error))
        if result.path:
            return EditResult(
                path=self._virtual(result.path),
                occurrences=result.occurrences,
            )
        return result

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.to_thread(
            self.edit, file_path, old_string, new_string, replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        try:
            virtual = str(file_path or "/")
            if virtual in {"/", ""}:
                return DeleteResult(error="不允许删除会话根目录")
            result = self._sandbox.delete(self._abs(file_path))
        except ValueError as error:
            return DeleteResult(error=str(error))
        if result.path:
            return DeleteResult(path=self._virtual(result.path))
        return result

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            result = self._sandbox.grep(
                pattern, self._abs(path or "/"), glob, max_count=max_count
            )
        except ValueError as error:
            return GrepResult(error=str(error))
        matches: list[GrepMatch] = [
            {**match, "path": self._virtual(str(match.get("path", "")))}
            for match in (result.matches or [])
        ]
        return GrepResult(
            error=result.error, matches=matches, truncated=result.truncated
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(
            self.grep, pattern, path, glob, max_count=max_count
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            result = self._sandbox.glob(pattern, self._abs(path or "/"))
        except ValueError as error:
            return GlobResult(error=str(error))
        matches: list[FileInfo] = [
            {**match, "path": self._virtual(str(match.get("path", "")))}
            for match in (result.matches or [])
        ]
        return GlobResult(
            error=result.error,
            matches=matches,
            truncated=result.truncated,
            truncation_reason=result.truncation_reason,
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        try:
            self._ensure_root()
            mapped = [(self._abs(path), content) for path, content in files]
        except ValueError as error:
            return [FileUploadResponse(path=path, error=str(error)) for path, _ in files]
        responses = self._sandbox.upload_files(mapped)
        return [
            FileUploadResponse(
                path=self._virtual(response.path),
                error=response.error,
            )
            for response in responses
        ]

    async def aupload_files(
        self, files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            mapped = [self._abs(path) for path in paths]
        except ValueError as error:
            return [FileDownloadResponse(path=path, error=str(error)) for path in paths]
        responses = self._sandbox.download_files(mapped)
        return [
            FileDownloadResponse(
                path=self._virtual(response.path),
                content=response.content,
                error=response.error,
            )
            for response in responses
        ]

    async def adownload_files(
        self, paths: list[str],
    ) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self._ensure_root()
        return self._sandbox.execute(f"cd '{self._root()}' && {command}", timeout=timeout)

    async def aexecute(
        self, command: str, *, timeout: int | None = None,
    ) -> ExecuteResponse:
        return await asyncio.to_thread(self.execute, command, timeout=timeout)
