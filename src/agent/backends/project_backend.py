"""项目工作区后端：文件落本机项目，命令只走沙箱。

与旧的 ``HarnessWorkspaceBackend`` / ``WorkspaceFilesBackend`` 相比：

- 文件根不再固定为全局工作区，而是随每轮对话的 ``ProjectContext`` 变化；
- 不提供任何本机执行能力，``execute`` 只转发沙箱，未配置沙箱时直接失败；
- 写入不做扩展名黑名单，项目是用户真实目录，允许写入代码与脚本；
- ``.commonharness`` 是保留目录，Agent 不可读写。
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

from agent.backends.sandbox_files_backend import SandboxSessionFilesBackend
from agent.project_context import (
    ProjectContextError,
    current_project,
    require_writable_project,
    sandbox_project_dir,
)
from agent.project_marker import MARKER_DIRNAME

RESERVED_MESSAGE = ".commonharness 是系统保留目录，Agent 不可读写"

_MAX_FILE_SIZE_MB = 10

# 大目录默认不参与列举/搜索，避免 ls/glob/grep 被构建产物拖垮。
IGNORED_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "dist",
        "build",
        "__pycache__",
        MARKER_DIRNAME,
    }
)


def _is_ignored(path: str) -> bool:
    """判断路径是否落在内置忽略目录内。"""
    return any(
        part.lower() in IGNORED_NAMES
        for part in PurePosixPath(str(path)).parts
    )


class ProjectFilesBackend(FilesystemBackend):
    """锚定当前项目根的文件后端，不提供执行能力。"""

    def __init__(self) -> None:
        # 不调用父类 __init__：父类会给 self.cwd 赋值，而本类的 cwd 是动态属性。
        self.virtual_mode = True
        self.max_file_size_bytes = _MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def cwd(self) -> Path:
        """每次访问都按当前项目解析，避免把根固化到 Agent 图里。"""
        project = current_project()
        if project.root is None:
            raise ProjectContextError("自由会话没有本机项目目录")
        return project.root

    def _resolve_path(self, key: str) -> Path:
        """解析虚拟路径，并拒绝保留目录。"""
        full = super()._resolve_path(key)
        root = self.cwd
        try:
            relative = full.relative_to(root)
        except ValueError as error:
            raise ValueError("路径越出项目目录") from error
        if relative.parts and relative.parts[0].lower() == MARKER_DIRNAME:
            raise ValueError(RESERVED_MESSAGE)
        return full

    def _write_error(self) -> str | None:
        """返回写入前的项目状态错误；可写时返回 None。"""
        try:
            require_writable_project()
        except ProjectContextError as error:
            return str(error)
        return None

    # ---- 写操作守卫 ----

    def write(self, file_path: str, content: str) -> WriteResult:
        blocked = self._write_error()
        if blocked:
            return WriteResult(error=blocked)
        try:
            return super().write(file_path, content)
        except ValueError as error:
            return WriteResult(error=str(error))
        except OSError as error:
            return WriteResult(error=f"写入失败：{error}")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        blocked = self._write_error()
        if blocked:
            return WriteResult(error=blocked)
        try:
            return await super().awrite(file_path, content)
        except ValueError as error:
            return WriteResult(error=str(error))
        except OSError as error:
            return WriteResult(error=f"写入失败：{error}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        blocked = self._write_error()
        if blocked:
            return EditResult(error=blocked)
        try:
            return super().edit(file_path, old_string, new_string, replace_all)
        except ValueError as error:
            return EditResult(error=str(error))
        except OSError as error:
            return EditResult(error=f"编辑失败：{error}")

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        blocked = self._write_error()
        if blocked:
            return EditResult(error=blocked)
        try:
            return await super().aedit(file_path, old_string, new_string, replace_all)
        except ValueError as error:
            return EditResult(error=str(error))
        except OSError as error:
            return EditResult(error=f"编辑失败：{error}")

    def delete(self, file_path: str) -> DeleteResult:
        blocked = self._write_error()
        if blocked:
            return DeleteResult(error=blocked)
        try:
            target = self._resolve_path(file_path)
            if target == self.cwd:
                return DeleteResult(error="不允许删除项目根目录")
            return super().delete(file_path)
        except ValueError as error:
            return DeleteResult(error=str(error))
        except OSError as error:
            return DeleteResult(error=f"删除失败：{error}")

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        blocked = self._write_error()
        responses: list[FileUploadResponse | None] = [None] * len(files)
        allowed: list[tuple[int, str, bytes]] = []
        for index, (path, content) in enumerate(files):
            if blocked:
                responses[index] = FileUploadResponse(path=path, error=blocked)
                continue
            try:
                self._resolve_path(path)
            except ValueError as error:
                responses[index] = FileUploadResponse(path=path, error=str(error))
                continue
            allowed.append((index, path, content))
        if allowed:
            try:
                results = super().upload_files(
                    [(path, content) for _index, path, content in allowed]
                )
            except ValueError as error:
                for index, path, _content in allowed:
                    responses[index] = FileUploadResponse(path=path, error=str(error))
            else:
                for (index, _path, _content), result in zip(allowed, results):
                    responses[index] = result
        return [response for response in responses if response is not None]

    async def aupload_files(
        self, files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    # ---- 读操作：保留目录同样不可见 ----

    def ls(self, path: str) -> LsResult:
        try:
            result = super().ls(path)
        except ValueError as error:
            return LsResult(error=str(error))
        entries = [
            entry
            for entry in (result.entries or [])
            if not _is_ignored(str(entry.get("path", "")))
        ]
        return LsResult(error=result.error, entries=entries)

    async def als(self, path: str) -> LsResult:
        try:
            result = await super().als(path)
        except ValueError as error:
            return LsResult(error=str(error))
        entries = [
            entry
            for entry in (result.entries or [])
            if not _is_ignored(str(entry.get("path", "")))
        ]
        return LsResult(error=result.error, entries=entries)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            return super().read(file_path, offset=offset, limit=limit)
        except ValueError as error:
            return ReadResult(error=str(error))

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000,
    ) -> ReadResult:
        try:
            return await super().aread(file_path, offset=offset, limit=limit)
        except ValueError as error:
            return ReadResult(error=str(error))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            result = super().grep(pattern, path, glob, max_count=max_count)
        except ValueError as error:
            return GrepResult(error=str(error))
        matches = [
            match
            for match in (result.matches or [])
            if not _is_ignored(str(match.get("path", "")))
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
        try:
            result = await super().agrep(pattern, path, glob, max_count=max_count)
        except ValueError as error:
            return GrepResult(error=str(error))
        matches = [
            match
            for match in (result.matches or [])
            if not _is_ignored(str(match.get("path", "")))
        ]
        return GrepResult(
            error=result.error, matches=matches, truncated=result.truncated
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            result = super().glob(pattern, path)
        except ValueError as error:
            return GlobResult(error=str(error))
        matches = [
            match
            for match in (result.matches or [])
            if not _is_ignored(str(match.get("path", "")))
        ]
        return GlobResult(
            error=result.error,
            matches=matches,
            truncated=result.truncated,
            truncation_reason=result.truncation_reason,
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            result = await super().aglob(pattern, path)
        except ValueError as error:
            return GlobResult(error=str(error))
        matches = [
            match
            for match in (result.matches or [])
            if not _is_ignored(str(match.get("path", "")))
        ]
        return GlobResult(
            error=result.error,
            matches=matches,
            truncated=result.truncated,
            truncation_reason=result.truncation_reason,
        )


class UnavailableFilesBackend(BackendProtocol):
    """自由会话但未配置沙箱时的占位后端：所有文件操作返回同一错误。"""

    def __init__(self, message: str) -> None:
        self._message = message

    def ls(self, path: str) -> LsResult:
        return LsResult(error=self._message)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(error=self._message)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=self._message)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=self._message)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=self._message)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return GrepResult(error=self._message)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=self._message)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=self._message) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, error=self._message) for path in paths]


class ProjectWorkspaceBackend(SandboxBackendProtocol):
    """默认后端：文件走项目目录，``execute`` 只走沙箱。

    继承 ``SandboxBackendProtocol`` 是为了让 DeepAgents 注册 ``execute`` 工具；
    没有沙箱时调用会抛 ``NotImplementedError``，工具返回明确错误，绝不降级本机执行。
    """

    def __init__(self, sandbox: Any | None = None) -> None:
        self._project_files = ProjectFilesBackend()
        self._sandbox = sandbox
        self._sandbox_files = (
            SandboxSessionFilesBackend(sandbox) if sandbox is not None else None
        )

    def _active_files(self) -> Any:
        """按会话类型选择文件域：项目会话本机，自由会话沙箱。"""
        project = current_project()
        if project.kind == "free":
            if self._sandbox_files is None:
                return UnavailableFilesBackend(
                    "自由会话需要沙箱才能读写文件；请先在系统设置中配置沙箱服务"
                )
            return self._sandbox_files
        return self._project_files

    def _files_for(self, path: str | None) -> Any:
        """技能/AGENTS.md 属于用户级沙箱，不能被项目或会话文件域接管。"""
        text = str(path or "")
        if self._sandbox is not None and (
            text == "/AGENTS.md"
            or text.startswith("/skills")
            or text.startswith("/.myagent")
        ):
            return self._sandbox
        return self._active_files()

    @property
    def id(self) -> str:
        if self._sandbox is not None:
            try:
                return f"project+{self._sandbox.id}"
            except RuntimeError:
                pass
        return f"project:{current_project().id}"

    @property
    def files(self) -> Any:
        """暴露当前会话的文件后端，供上传/下载工具复用。"""
        return self._active_files()

    @property
    def sandbox(self) -> Any | None:
        """当前用户沙箱代理；未配置时为 None。"""
        return self._sandbox

    # ---- 文件操作全部委托项目后端 ----

    def ls(self, path: str) -> LsResult:
        return self._files_for(path).ls(path)

    async def als(self, path: str) -> LsResult:
        return await self._files_for(path).als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._files_for(file_path).read(file_path, offset=offset, limit=limit)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000,
    ) -> ReadResult:
        return await self._files_for(file_path).aread(
            file_path, offset=offset, limit=limit
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._files_for(path).grep(pattern, path, glob, max_count=max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await self._files_for(path).agrep(
            pattern, path, glob, max_count=max_count
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._files_for(path).glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._files_for(path).aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._files_for(file_path).write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._files_for(file_path).awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._files_for(file_path).edit(
            file_path, old_string, new_string, replace_all
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._files_for(file_path).aedit(
            file_path, old_string, new_string, replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        return self._files_for(file_path).delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._files_for(file_path).adelete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        target = files[0][0] if files else None
        return self._files_for(target).upload_files(files)

    async def aupload_files(
        self, files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        target = files[0][0] if files else None
        return await self._files_for(target).aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        target = paths[0] if paths else None
        return self._files_for(target).download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        target = paths[0] if paths else None
        return await self._files_for(target).adownload_files(paths)

    # ---- 执行：唯一允许的执行域是沙箱 ----

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if self._sandbox is None:
            raise NotImplementedError(
                "当前未配置沙箱，按安全策略禁止在本机执行任何命令；"
                "请先在系统设置中配置沙箱服务"
            )
        project = current_project()
        if project.kind == "free":
            return self._sandbox_files.execute(command, timeout=timeout)
        workdir = sandbox_project_dir(project.id)
        return self._sandbox.execute(f"cd '{workdir}' && {command}", timeout=timeout)

    async def aexecute(
        self, command: str, *, timeout: int | None = None,
    ) -> ExecuteResponse:
        if self._sandbox is None:
            raise NotImplementedError(
                "当前未配置沙箱，按安全策略禁止在本机执行任何命令；"
                "请先在系统设置中配置沙箱服务"
            )
        project = current_project()
        if project.kind == "free":
            return await self._sandbox_files.aexecute(command, timeout=timeout)
        workdir = sandbox_project_dir(project.id)
        return await self._sandbox.aexecute(f"cd '{workdir}' && {command}", timeout=timeout)


def agent_backend_available(backend: BackendProtocol) -> bool:
    """调试辅助：判断后端是否具备项目文件能力。"""
    return isinstance(backend, ProjectWorkspaceBackend)
